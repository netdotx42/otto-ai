import * as anchor from "@coral-xyz/anchor";
import { Program, BN } from "@coral-xyz/anchor";
import {
  Keypair,
  PublicKey,
  LAMPORTS_PER_SOL,
  SystemProgram,
  Transaction,
} from "@solana/web3.js";
import {
  TOKEN_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  createMint,
  createAccount,
  getAssociatedTokenAddressSync,
  getAccount,
  getMint,
  mintTo,
  createMintToInstruction,
} from "@solana/spl-token";
import { expect } from "chai";
import { Agent98Pass } from "../target/types/agent98_pass";

const PRICE = 1_980_000n;
const BPF_LOADER_UPGRADEABLE = new PublicKey("BPFLoaderUpgradeab1e11111111111111111111111");

describe("agent98_pass", () => {
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);
  const program = anchor.workspace.Agent98Pass as Program<Agent98Pass>;
  const conn = provider.connection;
  const admin = (provider.wallet as anchor.Wallet).payer;

  const [configPda] = PublicKey.findProgramAddressSync([Buffer.from("config")], program.programId);
  const [passMint] = PublicKey.findProgramAddressSync([Buffer.from("pass_mint")], program.programId);
  const [programData] = PublicKey.findProgramAddressSync(
    [program.programId.toBuffer()],
    BPF_LOADER_UPGRADEABLE,
  );

  let usdcMint: PublicKey;
  let fakeUsdcMint: PublicKey;
  let treasuryOwner: Keypair;
  let treasuryUsdc: PublicKey;
  const alice = Keypair.generate();
  const mallory = Keypair.generate();
  let aliceUsdc: PublicKey;
  let malloryUsdc: PublicKey;
  let malloryFakeUsdc: PublicKey;

  async function airdrop(pk: PublicKey, sol = 10) {
    const sig = await conn.requestAirdrop(pk, sol * LAMPORTS_PER_SOL);
    await conn.confirmTransaction(sig, "confirmed");
  }

  async function expectError(p: Promise<unknown>, needle: string) {
    try {
      await p;
    } catch (e: any) {
      const text = `${e?.message ?? ""} ${(e?.logs ?? []).join(" ")} ${e?.error?.errorCode?.code ?? ""}`;
      expect(text, text).to.contain(needle);
      return;
    }
    expect.fail(`expected failure containing "${needle}"`);
  }

  function mintAccounts(buyer: PublicKey, buyerUsdc: PublicKey, overrides: Record<string, PublicKey> = {}) {
    return {
      buyer,
      config: configPda,
      usdcMint,
      buyerUsdc,
      treasuryUsdc,
      passMint,
      buyerPass: getAssociatedTokenAddressSync(passMint, buyer),
      usdcTokenProgram: TOKEN_PROGRAM_ID,
      passTokenProgram: TOKEN_PROGRAM_ID,
      associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID,
      systemProgram: SystemProgram.programId,
      ...overrides,
    };
  }

  before(async () => {
    await Promise.all([airdrop(alice.publicKey), airdrop(mallory.publicKey)]);
    usdcMint = await createMint(conn, admin, admin.publicKey, null, 6);
    fakeUsdcMint = await createMint(conn, admin, admin.publicKey, null, 6);
    treasuryOwner = Keypair.generate();
    treasuryUsdc = await createAccount(conn, admin, usdcMint, treasuryOwner.publicKey);
    aliceUsdc = await createAccount(conn, admin, usdcMint, alice.publicKey);
    malloryUsdc = await createAccount(conn, admin, usdcMint, mallory.publicKey);
    malloryFakeUsdc = await createAccount(conn, admin, fakeUsdcMint, mallory.publicKey);
    await mintTo(conn, admin, usdcMint, aliceUsdc, admin, 1_000_000_000_000n);
    await mintTo(conn, admin, usdcMint, malloryUsdc, admin, 10_000_000n);
    await mintTo(conn, admin, fakeUsdcMint, malloryFakeUsdc, admin, 1_000_000_000_000n);
  });

  it("rejects initialization by anyone other than the upgrade authority", async () => {
    await expectError(
      program.methods
        .initialize()
        .accountsPartial({
          admin: mallory.publicKey,
          config: configPda,
          usdcMint,
          treasuryUsdc: malloryUsdc,
          passMint,
          program: program.programId,
          programData,
          usdcTokenProgram: TOKEN_PROGRAM_ID,
          passTokenProgram: TOKEN_PROGRAM_ID,
          systemProgram: SystemProgram.programId,
        })
        .signers([mallory])
        .rpc(),
      "Unauthorized",
    );
  });

  it("initializes with a PDA mint authority and no freeze authority", async () => {
    await program.methods
      .initialize()
      .accountsPartial({
        admin: admin.publicKey,
        config: configPda,
        usdcMint,
        treasuryUsdc,
        passMint,
        program: program.programId,
        programData,
        usdcTokenProgram: TOKEN_PROGRAM_ID,
        passTokenProgram: TOKEN_PROGRAM_ID,
        systemProgram: SystemProgram.programId,
      })
      .rpc();
    const cfg = await program.account.config.fetch(configPda);
    expect(cfg.usdcMint.toBase58()).to.eq(usdcMint.toBase58());
    expect(cfg.treasuryUsdc.toBase58()).to.eq(treasuryUsdc.toBase58());
    expect(cfg.totalMinted.toNumber()).to.eq(0);
    const mint = await getMint(conn, passMint);
    expect(mint.decimals).to.eq(0);
    expect(mint.mintAuthority!.toBase58()).to.eq(configPda.toBase58());
    expect(mint.freezeAuthority).to.eq(null);
  });

  it("charges exactly 1.98 USDC per pass and routes payment to the treasury atomically", async () => {
    const before = await getAccount(conn, aliceUsdc);
    await program.methods
      .mintPass(new BN(3))
      .accountsPartial(mintAccounts(alice.publicKey, aliceUsdc))
      .signers([alice])
      .rpc();
    const after = await getAccount(conn, aliceUsdc);
    const treasury = await getAccount(conn, treasuryUsdc);
    const passAta = await getAccount(conn, getAssociatedTokenAddressSync(passMint, alice.publicKey));
    expect(before.amount - after.amount).to.eq(3n * PRICE);
    expect(treasury.amount).to.eq(3n * PRICE);
    expect(passAta.amount).to.eq(3n);
    const cfg = await program.account.config.fetch(configPda);
    expect(cfg.totalMinted.toNumber()).to.eq(3);
  });

  it("allows unlimited aggregate issuance and multiple passes per wallet", async () => {
    for (let i = 0; i < 6; i++) {
      await program.methods
        .mintPass(new BN(50))
        .accountsPartial(mintAccounts(alice.publicKey, aliceUsdc))
        .signers([alice])
        .rpc();
    }
    const cfg = await program.account.config.fetch(configPda);
    expect(cfg.totalMinted.toNumber()).to.eq(303);
    const mint = await getMint(conn, passMint);
    expect(mint.supply).to.eq(303n);
  });

  it("rejects quantity 0 and quantities above the per-tx batch limit", async () => {
    await expectError(
      program.methods.mintPass(new BN(0)).accountsPartial(mintAccounts(alice.publicKey, aliceUsdc)).signers([alice]).rpc(),
      "InvalidQuantity",
    );
    await expectError(
      program.methods.mintPass(new BN(51)).accountsPartial(mintAccounts(alice.publicKey, aliceUsdc)).signers([alice]).rpc(),
      "InvalidQuantity",
    );
    await expectError(
      program.methods
        .mintPass(new BN("18446744073709551615"))
        .accountsPartial(mintAccounts(alice.publicKey, aliceUsdc))
        .signers([alice])
        .rpc(),
      "InvalidQuantity",
    );
  });

  it("spoof: rejects payment with a non-canonical (fake) USDC mint", async () => {
    await expectError(
      program.methods
        .mintPass(new BN(1))
        .accountsPartial(mintAccounts(mallory.publicKey, malloryFakeUsdc, { usdcMint: fakeUsdcMint }))
        .signers([mallory])
        .rpc(),
      "NonCanonicalUsdc",
    );
  });

  it("spoof: rejects a fake treasury account (payment redirection)", async () => {
    await expectError(
      program.methods
        .mintPass(new BN(1))
        .accountsPartial(mintAccounts(mallory.publicKey, malloryUsdc, { treasuryUsdc: malloryUsdc }))
        .signers([mallory])
        .rpc(),
      "InvalidTreasury",
    );
  });

  it("spoof: rejects spending a token account the signer does not own", async () => {
    await expectError(
      program.methods
        .mintPass(new BN(1))
        .accountsPartial(mintAccounts(mallory.publicKey, aliceUsdc))
        .signers([mallory])
        .rpc(),
      "ConstraintTokenOwner",
    );
  });

  it("spoof: rejects a substitute pass mint", async () => {
    const otherMint = await createMint(conn, admin, mallory.publicKey, null, 0);
    await expectError(
      program.methods
        .mintPass(new BN(1))
        .accountsPartial(
          mintAccounts(mallory.publicKey, malloryUsdc, {
            passMint: otherMint,
            buyerPass: getAssociatedTokenAddressSync(otherMint, mallory.publicKey),
          }),
        )
        .signers([mallory])
        .rpc(),
      "Error",
    );
  });

  it("mint authority: no external signer can mint passes directly", async () => {
    const malloryPass = getAssociatedTokenAddressSync(passMint, mallory.publicKey);
    const ix = createMintToInstruction(passMint, malloryPass, mallory.publicKey, 1000);
    const tx = new Transaction().add(ix);
    let failed = false;
    try {
      await provider.sendAndConfirm(tx, [mallory]);
    } catch {
      failed = true;
    }
    expect(failed).to.eq(true);
    const mint = await getMint(conn, passMint);
    expect(mint.supply).to.eq(303n);
  });

  it("insufficient USDC balance fails atomically (no pass issued)", async () => {
    const poor = Keypair.generate();
    await airdrop(poor.publicKey);
    const poorUsdc = await createAccount(conn, admin, usdcMint, poor.publicKey);
    await mintTo(conn, admin, usdcMint, poorUsdc, admin, 1_000_000n);
    await expectError(
      program.methods.mintPass(new BN(1)).accountsPartial(mintAccounts(poor.publicKey, poorUsdc)).signers([poor]).rpc(),
      "insufficient funds",
    );
    const cfg = await program.account.config.fetch(configPda);
    expect(cfg.totalMinted.toNumber()).to.eq(303);
  });

  it("admin controls: only admin can pause; pause blocks minting", async () => {
    await expectError(
      program.methods.setPaused(true).accountsPartial({ admin: mallory.publicKey, config: configPda }).signers([mallory]).rpc(),
      "Unauthorized",
    );
    await program.methods.setPaused(true).accountsPartial({ admin: admin.publicKey, config: configPda }).rpc();
    await expectError(
      program.methods.mintPass(new BN(1)).accountsPartial(mintAccounts(alice.publicKey, aliceUsdc)).signers([alice]).rpc(),
      "Paused",
    );
    await program.methods.setPaused(false).accountsPartial({ admin: admin.publicKey, config: configPda }).rpc();
    await program.methods.mintPass(new BN(1)).accountsPartial(mintAccounts(alice.publicKey, aliceUsdc)).signers([alice]).rpc();
  });

  it("treasury rotation only accepts a USDC token account and only from admin", async () => {
    const newTreasury = await createAccount(conn, admin, usdcMint, Keypair.generate().publicKey);
    const fakeTreasury = await createAccount(conn, admin, fakeUsdcMint, Keypair.generate().publicKey);
    await expectError(
      program.methods
        .setTreasury()
        .accountsPartial({ admin: mallory.publicKey, config: configPda, usdcMint, newTreasuryUsdc: newTreasury })
        .signers([mallory])
        .rpc(),
      "Unauthorized",
    );
    await expectError(
      program.methods
        .setTreasury()
        .accountsPartial({ admin: admin.publicKey, config: configPda, usdcMint, newTreasuryUsdc: fakeTreasury })
        .rpc(),
      "ConstraintTokenMint",
    );
    await program.methods
      .setTreasury()
      .accountsPartial({ admin: admin.publicKey, config: configPda, usdcMint, newTreasuryUsdc: newTreasury })
      .rpc();
    await program.methods
      .mintPass(new BN(2))
      .accountsPartial(mintAccounts(alice.publicKey, aliceUsdc, { treasuryUsdc: newTreasury }))
      .signers([alice])
      .rpc();
    expect((await getAccount(conn, newTreasury)).amount).to.eq(2n * PRICE);
    treasuryUsdc = newTreasury;
  });
});
