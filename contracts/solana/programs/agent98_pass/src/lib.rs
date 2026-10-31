//! AGENT//98 Utility Pass — Solana program.
//!
//! * Price: exactly 1.98 USDC (1_980_000 base units, 6 decimals) per pass.
//! * No aggregate supply cap. `MAX_BATCH` bounds a single instruction for transaction safety.
//! * USDC payment and pass issuance happen atomically in one instruction.
//! * The pass mint authority is a program-derived address; nobody (including the admin) can
//!   mint passes without paying through `mint_pass`.
#![allow(unexpected_cfgs)]

use anchor_lang::prelude::*;
use anchor_spl::associated_token::AssociatedToken;
use anchor_spl::token_interface::{
    self, Mint, MintTo, TokenAccount, TokenInterface, TransferChecked,
};

declare_id!("ARd3pfR6GHqpLYr7sacHgjNwwagkzN15qmoq2GnHyw2Z");

pub const PRICE_PER_PASS: u64 = 1_980_000;
pub const USDC_DECIMALS: u8 = 6;
pub const MAX_BATCH: u64 = 50;
pub const CONFIG_SEED: &[u8] = b"config";
pub const PASS_MINT_SEED: &[u8] = b"pass_mint";

/// Canonical USDC mints. Localnet builds (no feature) accept a test mint at initialization,
/// but cluster builds are pinned to Circle's canonical USDC mint.
#[cfg(feature = "mainnet")]
pub const CANONICAL_USDC: Option<Pubkey> =
    Some(pubkey!("EPjFWdd5AufqSSqeM2qJ1zzybapC8G4wEGGkZwyTDt1v"));
#[cfg(all(feature = "devnet", not(feature = "mainnet")))]
pub const CANONICAL_USDC: Option<Pubkey> =
    Some(pubkey!("4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"));
#[cfg(not(any(feature = "mainnet", feature = "devnet")))]
pub const CANONICAL_USDC: Option<Pubkey> = None;

#[program]
pub mod agent98_pass {
    use super::*;

    /// One-time initialization. Only the program's upgrade authority may call it, which
    /// prevents a front-running attacker from initializing the config with their own treasury.
    pub fn initialize(ctx: Context<Initialize>) -> Result<()> {
        if let Some(canonical) = CANONICAL_USDC {
            require_keys_eq!(ctx.accounts.usdc_mint.key(), canonical, PassError::NonCanonicalUsdc);
        }
        require!(
            ctx.accounts.usdc_mint.decimals == USDC_DECIMALS,
            PassError::InvalidUsdcDecimals
        );
        let config = &mut ctx.accounts.config;
        config.admin = ctx.accounts.admin.key();
        config.pending_admin = Pubkey::default();
        config.usdc_mint = ctx.accounts.usdc_mint.key();
        config.treasury_usdc = ctx.accounts.treasury_usdc.key();
        config.pass_mint = ctx.accounts.pass_mint.key();
        config.total_minted = 0;
        config.paused = false;
        config.bump = ctx.bumps.config;
        config.mint_bump = ctx.bumps.pass_mint;
        emit!(ConfigInitialized {
            admin: config.admin,
            usdc_mint: config.usdc_mint,
            treasury_usdc: config.treasury_usdc,
            pass_mint: config.pass_mint,
        });
        Ok(())
    }

    /// Pay `quantity * 1.98 USDC` to the treasury and receive `quantity` passes, atomically.
    pub fn mint_pass(ctx: Context<MintPass>, quantity: u64) -> Result<()> {
        require!(!ctx.accounts.config.paused, PassError::Paused);
        require!(quantity >= 1 && quantity <= MAX_BATCH, PassError::InvalidQuantity);
        let cost = quantity
            .checked_mul(PRICE_PER_PASS)
            .ok_or(PassError::MathOverflow)?;

        // 1) Payment: buyer -> treasury, decimals-checked.
        token_interface::transfer_checked(
            CpiContext::new(
                ctx.accounts.usdc_token_program.to_account_info(),
                TransferChecked {
                    from: ctx.accounts.buyer_usdc.to_account_info(),
                    mint: ctx.accounts.usdc_mint.to_account_info(),
                    to: ctx.accounts.treasury_usdc.to_account_info(),
                    authority: ctx.accounts.buyer.to_account_info(),
                },
            ),
            cost,
            USDC_DECIMALS,
        )?;

        // 2) Issuance: PDA-signed mint_to. Both CPIs succeed or the whole tx reverts.
        let bump = [ctx.accounts.config.bump];
        let signer_seeds: &[&[&[u8]]] = &[&[CONFIG_SEED, &bump]];
        token_interface::mint_to(
            CpiContext::new_with_signer(
                ctx.accounts.pass_token_program.to_account_info(),
                MintTo {
                    mint: ctx.accounts.pass_mint.to_account_info(),
                    to: ctx.accounts.buyer_pass.to_account_info(),
                    authority: ctx.accounts.config.to_account_info(),
                },
                signer_seeds,
            ),
            quantity,
        )?;

        let config = &mut ctx.accounts.config;
        config.total_minted = config
            .total_minted
            .checked_add(quantity)
            .ok_or(PassError::MathOverflow)?;

        emit!(PassMinted {
            buyer: ctx.accounts.buyer.key(),
            quantity,
            paid: cost,
            total_minted: config.total_minted,
        });
        Ok(())
    }

    pub fn set_paused(ctx: Context<AdminOnly>, paused: bool) -> Result<()> {
        ctx.accounts.config.paused = paused;
        emit!(PausedChanged { paused });
        Ok(())
    }

    pub fn set_treasury(ctx: Context<SetTreasury>) -> Result<()> {
        let config = &mut ctx.accounts.config;
        let previous = config.treasury_usdc;
        config.treasury_usdc = ctx.accounts.new_treasury_usdc.key();
        emit!(TreasuryUpdated { previous, current: config.treasury_usdc });
        Ok(())
    }

    pub fn propose_admin(ctx: Context<AdminOnly>, new_admin: Pubkey) -> Result<()> {
        require_keys_neq!(new_admin, Pubkey::default(), PassError::InvalidAdmin);
        ctx.accounts.config.pending_admin = new_admin;
        Ok(())
    }

    pub fn accept_admin(ctx: Context<AcceptAdmin>) -> Result<()> {
        let config = &mut ctx.accounts.config;
        config.admin = ctx.accounts.pending_admin.key();
        config.pending_admin = Pubkey::default();
        Ok(())
    }
}

#[account]
#[derive(InitSpace)]
pub struct Config {
    pub admin: Pubkey,
    pub pending_admin: Pubkey,
    pub usdc_mint: Pubkey,
    pub treasury_usdc: Pubkey,
    pub pass_mint: Pubkey,
    pub total_minted: u64,
    pub paused: bool,
    pub bump: u8,
    pub mint_bump: u8,
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(mut)]
    pub admin: Signer<'info>,

    #[account(
        init,
        payer = admin,
        space = 8 + Config::INIT_SPACE,
        seeds = [CONFIG_SEED],
        bump
    )]
    pub config: Account<'info, Config>,

    #[account(mint::token_program = usdc_token_program)]
    pub usdc_mint: InterfaceAccount<'info, Mint>,

    #[account(
        token::mint = usdc_mint,
        token::token_program = usdc_token_program
    )]
    pub treasury_usdc: InterfaceAccount<'info, TokenAccount>,

    /// Pass mint: 0 decimals, mint authority = config PDA, no freeze authority.
    #[account(
        init,
        payer = admin,
        seeds = [PASS_MINT_SEED],
        bump,
        mint::decimals = 0,
        mint::authority = config,
        mint::token_program = pass_token_program
    )]
    pub pass_mint: InterfaceAccount<'info, Mint>,

    #[account(
        constraint = program.programdata_address()? == Some(program_data.key()) @ PassError::Unauthorized
    )]
    pub program: Program<'info, crate::program::Agent98Pass>,

    #[account(
        constraint = program_data.upgrade_authority_address == Some(admin.key()) @ PassError::Unauthorized
    )]
    pub program_data: Account<'info, ProgramData>,

    pub usdc_token_program: Interface<'info, TokenInterface>,
    pub pass_token_program: Interface<'info, TokenInterface>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct MintPass<'info> {
    #[account(mut)]
    pub buyer: Signer<'info>,

    #[account(
        mut,
        seeds = [CONFIG_SEED],
        bump = config.bump,
        has_one = usdc_mint @ PassError::NonCanonicalUsdc,
        has_one = treasury_usdc @ PassError::InvalidTreasury,
        has_one = pass_mint @ PassError::InvalidPassMint
    )]
    pub config: Account<'info, Config>,

    #[account(mint::token_program = usdc_token_program)]
    pub usdc_mint: InterfaceAccount<'info, Mint>,

    #[account(
        mut,
        token::mint = usdc_mint,
        token::authority = buyer,
        token::token_program = usdc_token_program
    )]
    pub buyer_usdc: InterfaceAccount<'info, TokenAccount>,

    #[account(
        mut,
        token::mint = usdc_mint,
        token::token_program = usdc_token_program
    )]
    pub treasury_usdc: InterfaceAccount<'info, TokenAccount>,

    #[account(
        mut,
        seeds = [PASS_MINT_SEED],
        bump = config.mint_bump,
        mint::authority = config,
        mint::token_program = pass_token_program
    )]
    pub pass_mint: InterfaceAccount<'info, Mint>,

    #[account(
        init_if_needed,
        payer = buyer,
        associated_token::mint = pass_mint,
        associated_token::authority = buyer,
        associated_token::token_program = pass_token_program
    )]
    pub buyer_pass: InterfaceAccount<'info, TokenAccount>,

    pub usdc_token_program: Interface<'info, TokenInterface>,
    pub pass_token_program: Interface<'info, TokenInterface>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct AdminOnly<'info> {
    pub admin: Signer<'info>,
    #[account(mut, seeds = [CONFIG_SEED], bump = config.bump, has_one = admin @ PassError::Unauthorized)]
    pub config: Account<'info, Config>,
}

#[derive(Accounts)]
pub struct SetTreasury<'info> {
    pub admin: Signer<'info>,
    #[account(
        mut,
        seeds = [CONFIG_SEED],
        bump = config.bump,
        has_one = admin @ PassError::Unauthorized,
        has_one = usdc_mint @ PassError::NonCanonicalUsdc
    )]
    pub config: Account<'info, Config>,
    pub usdc_mint: InterfaceAccount<'info, Mint>,
    #[account(token::mint = usdc_mint)]
    pub new_treasury_usdc: InterfaceAccount<'info, TokenAccount>,
}

#[derive(Accounts)]
pub struct AcceptAdmin<'info> {
    pub pending_admin: Signer<'info>,
    #[account(
        mut,
        seeds = [CONFIG_SEED],
        bump = config.bump,
        constraint = config.pending_admin == pending_admin.key() @ PassError::Unauthorized
    )]
    pub config: Account<'info, Config>,
}

#[event]
pub struct ConfigInitialized {
    pub admin: Pubkey,
    pub usdc_mint: Pubkey,
    pub treasury_usdc: Pubkey,
    pub pass_mint: Pubkey,
}

#[event]
pub struct PassMinted {
    pub buyer: Pubkey,
    pub quantity: u64,
    pub paid: u64,
    pub total_minted: u64,
}

#[event]
pub struct PausedChanged {
    pub paused: bool,
}

#[event]
pub struct TreasuryUpdated {
    pub previous: Pubkey,
    pub current: Pubkey,
}

#[error_code]
pub enum PassError {
    #[msg("Quantity must be between 1 and MAX_BATCH")]
    InvalidQuantity,
    #[msg("Arithmetic overflow")]
    MathOverflow,
    #[msg("Minting is paused")]
    Paused,
    #[msg("USDC mint is not the canonical mint for this deployment")]
    NonCanonicalUsdc,
    #[msg("USDC mint must have 6 decimals")]
    InvalidUsdcDecimals,
    #[msg("Treasury token account does not match config")]
    InvalidTreasury,
    #[msg("Pass mint does not match config")]
    InvalidPassMint,
    #[msg("Unauthorized")]
    Unauthorized,
    #[msg("Invalid admin")]
    InvalidAdmin,
}
