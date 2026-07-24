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

Unauthenticated navigation uses the History API for `/login`, `/register`, `/forgot-password`, `/terms`, and `/privacy`. These views and the authenticated sidebar share `components/BrandLockup.tsx`; Vite injects the displayed version from `package.json`. Registration stays visibly disabled when the public readiness endpoint is closed. Password recovery verifies the six-digit email code before rendering new-password fields and keeps the resulting one-time reset grant only in component memory. The Settings page contains the administrator-only SMTP receipt confirmation, registration switch, ordinary-user capacity, and user enablement controls.

Opening the account dialog creates no login session. Its QR panel first presents two explicit choices: recommended local headed Chrome through the unified official-login session, or the existing web QR flow for remote access. Local Chrome QR and SMS share the official-session poller, terminal states, “open on this Mac” action, and cancellation; web QR keeps its own generate/check/continue contract and server TTL. Closing the dialog, changing methods, or unmounting stops the matching poller and cancels an active local Chrome session. Password and manual Cookie remain secondary methods. Account renewal reuses the official browser profile and reads saved credentials only after the profile is fully logged out.

## Notes

- Keep `base: '/static/'` in `vite.config.ts`, because the FastAPI app serves bundled assets under `/static/`.
- Production source maps require the explicit `VITE_BUILD_SOURCEMAP=true` opt-in.
- The build retains only the current and previous successful asset generations.
- Do not add API keys, Xianyu cookies, or deployment tokens to `.env.local` or frontend source.
