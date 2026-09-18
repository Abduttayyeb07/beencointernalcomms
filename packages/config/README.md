# Shared Config

This package is reserved for the future TypeScript, ESLint, Tailwind, and test presets used by
`apps/web`, `apps/api`, and shared packages.

The current workspace includes a dependency-free web build because the local machine does not
have Node/npm/pnpm available on PATH. When the toolchain is installed, this folder should hold:

- `tsconfig/base.json`
- `eslint/base.js`
- `tailwind/preset.ts`
- `vitest/base.ts`
