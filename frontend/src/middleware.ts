import { jwtVerify } from "jose";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import type { Role } from "@/lib/types";

const COOKIE_NAME = "claimflow_session";

/** Must match the backend HS256 secret (backend/app/config.py, Settings.jwt_secret). */
const DEFAULT_DEV_SECRET = "dev-only-secret-change-me-0123456789abcdef";

const ROUTE_ROLES: ReadonlyArray<readonly [prefix: string, role: Role]> = [
  ["/claimant", "claimant"],
  ["/imaging", "imaging_specialist"],
  ["/specialist", "medical_specialist"],
  ["/agent", "insurance_agent"],
];

function jwtSecret(): Uint8Array {
  return new TextEncoder().encode(process.env.JWT_SECRET ?? DEFAULT_DEV_SECRET);
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const { pathname } = request.nextUrl;
  if (pathname === "/login") {
    return NextResponse.next();
  }

  const guard = ROUTE_ROLES.find(
    ([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
  if (!guard) {
    return NextResponse.next();
  }

  const loginUrl = new URL("/login", request.url);
  const token = request.cookies.get(COOKIE_NAME)?.value;
  if (!token) {
    return NextResponse.redirect(loginUrl);
  }

  try {
    const { payload } = await jwtVerify(token, jwtSecret(), { algorithms: ["HS256"] });
    if (payload.role !== guard[1]) {
      return NextResponse.redirect(loginUrl);
    }
  } catch {
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  // Everything except the API proxy, Next internals, and static assets (paths with a dot).
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
