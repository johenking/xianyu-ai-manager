# Xianyu AI Manager frontend

React/Vite frontend for the FastAPI backend in the repository root.

## Commands

```bash
npm ci
npm run dev
npm run typecheck
npm test
npm run build
npm run verify:build
```

`npm run dev` serves the frontend on `http://localhost:3000` and proxies API requests to `http://localhost:8091` through `vite.config.ts`.

`npm run build` writes production assets to `../static`; the backend serves those assets directly through `Start.py`. Business pages are lazy-loaded, while `services/api.ts` and `types.ts` keep compatibility exports for the domain modules under `services/api/` and `types/`.

Unauthenticated navigation uses the History API for `/login`, `/register`, `/forgot-password`, `/terms`, and `/privacy`. Registration stays visibly disabled when the public readiness endpoint is closed. The Settings page contains the administrator-only SMTP readiness, invite, registration-switch, and ordinary-user controls.

## Notes

- Keep `base: '/static/'` in `vite.config.ts`, because the FastAPI app serves bundled assets under `/static/`.
- Production source maps require the explicit `VITE_BUILD_SOURCEMAP=true` opt-in.
- The build retains only the current and previous successful asset generations.
- Do not add API keys, Xianyu cookies, or deployment tokens to `.env.local` or frontend source.
