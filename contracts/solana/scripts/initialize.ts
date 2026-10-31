/**
 * One-time initialization of the deployed agent98_pass program.
 *   ANCHOR_PROVIDER_URL=https://api.devnet.solana.com ANCHOR_WALLET=~/.config/solana/upgrade-authority.json \
 *   TREASURY_OWNER=<treasury wallet pubkey> pnpm exec ts-node scripts/initialize.ts
 * The signer MUST be the program's upgrade authority (enforced on-chain).
 * USDC mint is fixed per cluster; the program build feature (devnet/mainnet) also pins it on-chain.
 */
import * as anchor from "@coral-xyz/anchor";
import { PublicKey, SystemProgram } from "@solana/web3.js";
import { TOKEN_PROGRAM_ID, getOrCreateAssociatedTokenAccount } from "@solana/spl-token";
import idl from "../target/idl/agent98_pass.json";

const USDC: Record<string, string> = {
  devnet: "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
  mainnet: "EPjFWdd5AufqSSqeM2qJ1zzybapC8G4wEGGkZwyTDt1v",
};

async function main() {
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);
  const program = new anchor.Program(idl as anchor.Idl, provider);
  const cluster = provider.connection.rpcEndpoint.includes("mainnet") ? "mainnet" : "devnet";
  const usdcMint = new PublicKey(process.env.USDC_MINT ?? USDC[cluster]);
  const treasuryOwner = new PublicKey(process.env.TREASURY_OWNER!);
  const payer = (provider.wallet as anchor.Wallet).payer;
  const treasury = await getOrCreateAssociatedTokenAccount(provider.connection, payer, usdcMint, treasuryOwner);
  const [config] = PublicKey.findProgramAddressSync([Buffer.from("config")], program.programId);
  const [passMint] = PublicKey.findProgramAddressSync([Buffer.from("pass_mint")], program.programId);
  const [programData] = PublicKey.findProgramAddressSync([program.programId.toBuffer()], new PublicKey("BPFLoaderUpgradeab1e11111111111111111111111"));
  const sig = await program.methods
    .initialize()
    .accountsPartial({
      admin: payer.publicKey, config, usdcMint, treasuryUsdc: treasury.address, passMint, program: program.programId, programData,
      usdcTokenProgram: TOKEN_PROGRAM_ID, passTokenProgram: TOKEN_PROGRAM_ID, systemProgram: SystemProgram.programId,
    })
    .rpc();
  console.log(JSON.stringify({ cluster, program: program.programId.toBase58(), config: config.toBase58(), passMint: passMint.toBase58(), treasuryUsdc: treasury.address.toBase58(), signature: sig }, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
