import { api } from '../api/client';
import { program, count, safeText, shortTime } from '../program/data';
jest.mock('../api/client', () => ({ api: { get: jest.fn() } }));

describe('program pilot is read-only', () => {
  beforeEach(() => jest.clearAllMocks());
  it('only calls existing authenticated GET routes', async () => {
    (api.get as jest.Mock).mockResolvedValue({ items: [] });
    await program.overview('needs_reply');
    await program.pipeline();
    await program.visits();
    await program.family('a/b c');
    expect((api.get as jest.Mock).mock.calls.map(c => c[0])).toEqual([
      '/program/command?filter=needs_reply&limit=40',
      '/program/command/pipeline',
      '/program/appointments?when=upcoming',
      '/program/command/family/a%2Fb%20c',
    ]);
    expect((api as { post?: unknown }).post).toBeUndefined();
  });
  it('never substitutes made-up counts for unavailable data', () => {
    expect(count(null)).toBeNull();
    expect(count(undefined)).toBeNull();
    expect(count('12')).toBeNull();
    expect(count(0)).toBe(0);
  });
  it('handles missing and invalid reply timestamps', () => {
    expect(shortTime(null)).toBe('Time not available');
    expect(shortTime('not-a-time')).toBe('Time not available');
    expect(safeText('  ')).toBe('No message preview');
  });
});
