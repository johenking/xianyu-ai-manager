# xianyu-super-butler frontend

React/Vite frontend for the FastAPI backend in the repository root.

## Commands

```bash
npm install
npm run dev
npm run build
```

`npm run dev` serves the frontend on `http://localhost:3000` and proxies API requests to `http://localhost:8091` through `vite.config.ts`.

`npm run build` writes production assets to `../static`; the backend serves those assets directly through `Start.py`.

## Notes

- Keep `base: '/static/'` in `vite.config.ts`, because the FastAPI app serves bundled assets under `/static/`.
- Do not add API keys, Xianyu cookies, or deployment tokens to `.env.local` or frontend source.
