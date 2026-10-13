import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { publishRelease, resolveImageDigest } from './publish-release.mjs';

const sha = 'a'.repeat(40);
const file = { name: 'refiner.deb', bytes: Buffer.from('qualified package') };
const digest = `sha256:${createHash('sha256').update(file.bytes).digest('hex')}`;
function fixture(options = {}) {
  const release = { id: 42, tag_name: 'v0.1.0-dev.162.1.aaaaaaa', draft: false,
    prerelease: true, assets: [], ...options.release };
  let exists = !options.absent;
  let tagExists = exists;
  const calls = [];
  const config = { repository: 'neuralmimicry/rag_demo', sha, tag: release.tag_name,
    token: 'fixture-private-token', files: [file], prerelease: true, runId: '1', attempt: '1',
    fetchImpl: async (url, init) => {
      calls.push({ url, method: init.method });
      const path = new URL(url).pathname;
      if (init.method === 'GET' && path.includes('/git/ref/tags/')) {
        return tagExists ? Response.json({ object: { type: options.annotated ? 'tag' : 'commit',
          sha: options.annotated ? 'c'.repeat(40) : options.tagSha ?? sha } }) : new Response('', { status: 404 });
      }
      if (init.method === 'GET' && path.includes('/git/tags/')) {
        return Response.json({ object: { type: options.tagCycle ? 'tag' : 'commit',
          sha: options.tagCycle ? 'c'.repeat(40) : options.tagSha ?? sha } });
      }
      if (init.method === 'GET') return exists ? Response.json(release) : new Response('', { status: 404 });
      if (path.endsWith('/git/refs')) {
        tagExists = true;
        if (options.lostTag) throw Error('lost reply');
        return Response.json({ ref: `refs/tags/${release.tag_name}`, object: { sha } }, { status: 201 });
      }
      if (path.endsWith('/releases')) {
        exists = true;
        if (options.lostCreation) throw Error('lost reply');
        return Response.json(release, { status: 201 });
      }
      const asset = { name: file.name, digest };
      release.assets.push(asset);
      if (options.lostUpload) throw Error('lost reply');
      return Response.json(asset, { status: 201 });
    } };
  return { config, calls, release };
}
test('existing source tag and matching asset are read-only', async () => {
  const f = fixture({ release: { assets: [{ name: file.name, digest }] } });
  await publishRelease(f.config);
  assert.ok(f.calls.every(call => call.method === 'GET'));
});
test('a stale tag blocks all release and asset writes', async () => {
  const f = fixture({ tagSha: 'b'.repeat(40) });
  await assert.rejects(publishRelease(f.config), /qualified source/);
  assert.ok(f.calls.every(call => call.method === 'GET'));
});
test('annotated GitHub tags resolve to their commit before publication', async () => {
  const f = fixture({ annotated: true });
  await publishRelease(f.config);
  assert.ok(f.calls.some(call => call.url.includes('/git/tags/')));
  assert.ok(f.calls.every(call => !call.url.includes('/commits/')));
});
test('unresolvable annotation cycles block all writes', async () => {
  const f = fixture({ annotated: true, tagCycle: true });
  await assert.rejects(publishRelease(f.config), /cannot be resolved/);
  assert.ok(f.calls.every(call => call.method === 'GET'));
});
test('different or missing existing digests never clobber assets', async () => {
  for (const existing of ['sha256:wrong', undefined]) {
    const f = fixture({ release: { assets: [{ name: file.name, digest: existing }] } });
    await assert.rejects(publishRelease(f.config), /asset differs/);
    assert.ok(f.calls.every(call => call.method === 'GET'));
  }
});
test('lost release creation is reconciled without a duplicate creation', async () => {
  const f = fixture({ absent: true, lostCreation: true });
  await publishRelease(f.config);
  assert.equal(f.calls.filter(call => call.method === 'POST' && call.url.endsWith('/releases')).length, 1);
});
test('lost tag creation is reconciled and never force-updated', async () => {
  const f = fixture({ absent: true, lostTag: true });
  await publishRelease(f.config);
  assert.equal(f.calls.filter(call => call.url.endsWith('/git/refs')).length, 1);
  assert.ok(f.calls.every(call => ['GET', 'POST'].includes(call.method)));
});
test('lost upload response is reconciled without a duplicate upload', async () => {
  const f = fixture({ lostUpload: true });
  await publishRelease(f.config);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(f.release.assets.length, 1);
});
test('invalid identities and duplicate asset names make no requests', async () => {
  for (const change of [{ repository: 'attacker/path/extra' }, { tag: 'latest-main' },
    { sha: 'short' }, { files: [file, file] }]) {
    const f = fixture();
    await assert.rejects(publishRelease({ ...f.config, ...change }));
    assert.equal(f.calls.length, 0);
  }
});
test('publishing keeps credentials on fixed GitHub origins and rejects redirects', async () => {
  const f = fixture();
  const fetchImpl = f.config.fetchImpl;
  f.config.fetchImpl = (url, init) => {
    assert.ok(['https://api.github.com', 'https://uploads.github.com'].includes(new URL(url).origin));
    assert.equal(init.redirect, 'error');
    return fetchImpl(url, init);
  };
  await publishRelease(f.config);
});

function imageFixture({ wrongSha = false, corruptManifest = false, redirect } = {}) {
  const config = Buffer.from(JSON.stringify({ os: 'linux', architecture: 'amd64', config: { Labels: {
    'org.opencontainers.image.revision': wrongSha ? 'b'.repeat(40) : sha,
    'org.opencontainers.image.source': 'https://github.com/neuralmimicry/rag_demo'
  } } }));
  const configDigest = `sha256:${createHash('sha256').update(config).digest('hex')}`;
  const manifest = Buffer.from(JSON.stringify({ config: { digest: configDigest } }));
  const manifestDigest = `sha256:${createHash('sha256').update(manifest).digest('hex')}`;
  return { expected: manifestDigest, options: { image: 'ghcr.io/neuralmimicry/refiner:v0.1.0-dev.162.1.aaaaaaa-amd64',
    sha, username: 'fixture', password: 'private-fixture', expectedArchitecture: 'amd64', fetchImpl: async (url, init) => {
      if (new URL(url).hostname === 'pkg-containers.githubusercontent.com') {
        assert.equal(init.headers?.Authorization, undefined);
        assert.equal(init.redirect, 'error');
        return new Response(config);
      }
      assert.equal(new URL(url).origin, 'https://ghcr.io');
      assert.equal(init.redirect, url.includes('/blobs/') ? 'manual' : 'error');
      if (url.includes('/token?')) return Response.json({ token: 'private-registry-fixture' });
      if (url.includes('/blobs/')) return redirect ? new Response('', { status: 307, headers: { location: redirect } }) : new Response(config);
      return new Response(manifest, { headers: { 'docker-content-digest': corruptManifest ? 'sha256:wrong' : manifestDigest } });
    } } };
}
test('registry digest is checked against bytes and the image source revision', async () => {
  const f = imageFixture();
  assert.equal(await resolveImageDigest(f.options), f.expected);
});
test('registry metadata cannot attest a different source or corrupt digest', async () => {
  for (const change of [{ wrongSha: true }, { corruptManifest: true }]) {
    await assert.rejects(resolveImageDigest(imageFixture(change).options));
  }
});
test('an image config must match its promised native architecture', async () => {
  const f = imageFixture();
  await assert.rejects(resolveImageDigest({ ...f.options, expectedArchitecture: 'arm64' }));
});
test('signed registry CDN blobs are verified without forwarding credentials', async () => {
  const f = imageFixture({ redirect: 'https://pkg-containers.githubusercontent.com/ghcr1/blobs/config?fixture=signature' });
  assert.equal(await resolveImageDigest(f.options), f.expected);
});
test('registry redirects cannot send credentials or signed URLs to another host', async () => {
  for (const redirect of ['https://attacker.example/blob', 'http://pkg-containers.githubusercontent.com/blob',
    'https://fixture:secret@pkg-containers.githubusercontent.com/blob']) {
    await assert.rejects(resolveImageDigest(imageFixture({ redirect }).options), /redirect is not trusted/);
  }
});
function indexFixture(architectures = ['amd64', 'arm64'], wrongConfig = false) {
  const documents = new Map();
  const register = object => {
    const bytes = Buffer.from(JSON.stringify(object));
    const digest = `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
    documents.set(digest, bytes);
    return digest;
  };
  const manifests = architectures.map(architecture => {
    const config = register({ os: 'linux', architecture: wrongConfig ? 'amd64' : architecture,
      config: { Labels: { 'org.opencontainers.image.revision': sha,
        'org.opencontainers.image.source': 'https://github.com/neuralmimicry/rag_demo' } } });
    return { platform: { os: 'linux', architecture }, digest: register({ config: { digest: config } }) };
  });
  const expected = register({ manifests });
  const options = { image: 'ghcr.io/neuralmimicry/refiner:v0.1.0-dev.162.1.aaaaaaa',
    sha, username: 'fixture', password: 'private-fixture', fetchImpl: async url => {
      if (url.includes('/token?')) return Response.json({ token: 'private-registry-fixture' });
      const digest = url.includes('sha256:') ? url.slice(url.indexOf('sha256:')) : expected;
      return new Response(documents.get(digest), { headers: { 'docker-content-digest': digest } });
    } };
  return { options, expected };
}
test('a complete index binds both native configurations to the exact source', async () => {
  const f = indexFixture();
  assert.equal(await resolveImageDigest(f.options), f.expected);
});
test('an incomplete or falsely labelled index cannot pass publication verification', async () => {
  for (const f of [indexFixture(['amd64']), indexFixture(['amd64', 'amd64']), indexFixture(undefined, true)]) {
    await assert.rejects(resolveImageDigest(f.options));
  }
});
