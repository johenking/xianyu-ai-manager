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

Opening the account dialog creates no login session. QR recommends web QR and offers server-side Chrome as a fallback. Web QR renders the official QR in the page without starting a browser; interactive risk control can continue in the server browser where the UI exposes it, or in the extension on a remote device. The extension is an isolated import and renewal entry that opens the official page in the user's own Chrome/Edge. Closing or hiding the dialog does not turn a QR scan or tab close into success: polling continues until a real platform Token is validated, the account identity and Cookie are persisted, and the account list confirms the result. Each browser path closes only the page or window it created after that confirmation. Missing or invalid extension handshakes create no server login session and offer install/refresh or web-QR fallback. Passwords are never collected by the console; after successful extension login, renewal credentials require a separate explicit opt-in and are encrypted and bound to one device. Manual Cookie and manual extension pairing remain advanced methods.

## Notes

- Keep `base: '/static/'` in `vite.config.ts`, because the FastAPI app serves bundled assets under `/static/`.
- Production source maps require the explicit `VITE_BUILD_SOURCEMAP=true` opt-in.
- The build retains only the current and previous successful asset generations.
- Do not add API keys, Xianyu cookies, or deployment tokens to `.env.local` or frontend source.
