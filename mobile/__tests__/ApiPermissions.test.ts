/**
 * Role-based access (viewer / operator / admin, see api/auth.py's require_role):
 * a 403 means "this account's role can't do that", not "the session is dead".
 * It must surface the server's reason and leave the user logged in -- otherwise
 * a viewer tapping Delete would be thrown back to the login screen with a
 * misleading "Session expired".
 */

function respond(status: number, body: Record<string, unknown>) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

describe('api request when the server refuses the action', () => {
  beforeEach(() => {
    jest.resetModules();
    jest.doMock('../services/auth', () => ({
      getAccessToken: jest.fn().mockResolvedValue('access-token'),
      refreshAccessToken: jest.fn().mockResolvedValue(undefined),
      logout: jest.fn().mockResolvedValue(undefined),
    }));
  });

  it('403 reports the server reason and keeps the user logged in', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      respond(403, { detail: 'This action requires the admin role.' }),
    ) as never;
    const api = require('../services/api');
    const auth = require('../services/auth');

    await expect(api.deleteGauge(1)).rejects.toThrow('This action requires the admin role.');
    expect(auth.logout).not.toHaveBeenCalled();
  });

  it('401 that survives a token refresh still forces a re-login', async () => {
    global.fetch = jest.fn().mockResolvedValue(respond(401, { detail: 'Token expired' })) as never;
    const api = require('../services/api');
    const auth = require('../services/auth');

    await expect(api.deleteGauge(1)).rejects.toThrow('Session expired. Please log in again.');
    expect(auth.logout).toHaveBeenCalled();
  });
});
