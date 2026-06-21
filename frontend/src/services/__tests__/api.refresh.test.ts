import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthenticationError, get, post } from '../api';

const REFRESH_URL = /\/auth\/refresh$/;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function freshTokens(): Record<string, unknown> {
  return { accessToken: 'fresh', refreshToken: 'refresh-2', expiresIn: 3600, tokenType: 'Bearer' };
}

function setStoredTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem('auth_token', accessToken);
  localStorage.setItem(
    'tot_auth_tokens',
    JSON.stringify({ accessToken, refreshToken, expiresIn: 3600, tokenType: 'Bearer' }),
  );
}

function authHeader(init?: RequestInit): string {
  const headers = init?.headers as Record<string, string> | undefined;
  return headers?.Authorization ?? '';
}

describe('guarded token refresh', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    localStorage.clear();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('deduplicates concurrent 401s into a single refresh request', async () => {
    setStoredTokens('expired', 'refresh-1');
    let refreshCalls = 0;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (REFRESH_URL.test(url)) {
        refreshCalls += 1;
        await new Promise((r) => setTimeout(r, 10));
        return jsonResponse(200, { data: { tokens: freshTokens() } });
      }
      return authHeader(init) === 'Bearer fresh'
        ? jsonResponse(200, { ok: true })
        : jsonResponse(401, { message: 'expired' });
    }) as typeof globalThis.fetch;

    const [a, b] = await Promise.all([get('/users/me'), get('/users/me')]);

    expect(refreshCalls).toBe(1);
    expect(a.status).toBe(200);
    expect(b.status).toBe(200);
    expect(localStorage.getItem('auth_token')).toBe('fresh');
  });

  it('retries the original request exactly once after a successful refresh', async () => {
    setStoredTokens('expired', 'refresh-1');
    let originalCalls = 0;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (REFRESH_URL.test(url)) {
        return jsonResponse(200, { data: { tokens: freshTokens() } });
      }
      originalCalls += 1;
      return authHeader(init) === 'Bearer fresh'
        ? jsonResponse(200, { value: 42 })
        : jsonResponse(401, { message: 'expired' });
    }) as typeof globalThis.fetch;

    const res = await get('/users/me');

    expect(originalCalls).toBe(2);
    expect(res.status).toBe(200);
    expect(res.data).toEqual({ value: 42 });
  });

  it('clears auth state and throws a typed error when refresh fails', async () => {
    setStoredTokens('expired', 'refresh-1');
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (REFRESH_URL.test(url)) {
        return jsonResponse(401, { message: 'refresh token invalid' });
      }
      return jsonResponse(401, { message: 'expired' });
    }) as typeof globalThis.fetch;

    await expect(post('/users/me', { x: 1 })).rejects.toThrow(AuthenticationError);
    expect(localStorage.getItem('auth_token')).toBeNull();
    expect(localStorage.getItem('tot_auth_tokens')).toBeNull();
  });

  it('does not loop when the refresh endpoint itself returns 401', async () => {
    setStoredTokens('expired', 'refresh-1');
    let refreshCalls = 0;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (REFRESH_URL.test(url)) {
        refreshCalls += 1;
        return jsonResponse(401, { message: 'no' });
      }
      return jsonResponse(401, { message: 'expired' });
    }) as typeof globalThis.fetch;

    await expect(get('/users/me')).rejects.toThrow(AuthenticationError);
    expect(refreshCalls).toBe(1);
  });
});
