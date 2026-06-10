# ClaimFlow Frontend

Next.js (App Router) portal for the ClaimFlow medical claims system.

## Development

```bash
npm install
npm run dev
```

The app runs at http://localhost:3000 and proxies `/api/*` to the FastAPI backend
(`BACKEND_URL`, default `http://localhost:8000`). Start the backend with `make dev-api`
from the repository root, or use `make dev-web` for this app.

## Environment

| Variable      | Default                                      | Purpose                            |
| ------------- | -------------------------------------------- | ---------------------------------- |
| `BACKEND_URL` | `http://localhost:8000`                      | Rewrite target for the API proxy   |
| `JWT_SECRET`  | backend dev default                          | HS256 secret for session cookies   |

## Structure

- `src/middleware.ts` - role-based route guards for `/claimant`, `/imaging`, `/specialist`, `/agent`
- `src/lib/api-client.ts` - typed fetch wrapper for the same-origin API proxy
- `src/lib/types.ts` - types mirroring backend response models
- `src/app/login` - sign-in page with demo accounts
- `src/app/imaging` - imaging review queue (polls every 2s)

## Checks

```bash
npx tsc --noEmit
npm run lint
npm run build
```
