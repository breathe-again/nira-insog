/// <reference types="vite/client" />

// Augment Vite's default ImportMetaEnv with the env vars we actually use.
// Keeps TypeScript honest about what's set vs. undefined at build time.
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
