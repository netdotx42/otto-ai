# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS deps
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/shared packages/shared
RUN pnpm install --frozen-lockfile --filter @agent98/web...

FROM deps AS build
ARG NEXT_PUBLIC_API_URL NEXT_PUBLIC_NETWORK_MODE=testnet NEXT_PUBLIC_BASE_PASS_ADDRESS NEXT_PUBLIC_SOLANA_RPC_URL NEXT_PUBLIC_BASE_RPC_URL NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID
ENV NEXT_TELEMETRY_DISABLED=1
COPY apps/web apps/web
RUN pnpm --filter @agent98/web build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./apps/web/.next/static
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
