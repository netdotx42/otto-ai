import type { NextConfig } from "next";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const config: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@agent98/shared"],
  output: "standalone",
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
  env: { NEXT_PUBLIC_API_URL: API },
  webpack: (cfg) => {
    // WalletConnect / pino optional deps that are not used in the browser bundle.
    cfg.externals.push("pino-pretty", "lokijs", "encoding");
    // @base-org/account (via @wagmi/connectors) optionally imports x402 payment SDKs we never use.
    cfg.resolve.alias = {
      ...cfg.resolve.alias,
      "@x402/core/client": false,
      "@x402/evm": false,
      "@x402/evm/exact/client": false,
      "@x402/evm/upto/client": false,
      "@x402/svm/exact/client": false,
    };
    return cfg;
  },
};

export default config;
