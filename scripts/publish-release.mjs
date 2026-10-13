import { readFile, readdir, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// CI glue uses Node's existing HTTP runtime; no downloaded publishing CLI.
export async function publishRelease({ repository, sha, tag, token, files, draft = false,
  prerelease = false, runId, attempt, fetchImpl = fetch }) {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository) || !/^[a-f0-9]{40}$/.test(sha)
      || !/^v[0-9][0-9A-Za-z.+-]{0,127}$/.test(tag) || !token || !files.length || files.length > 32) {
    throw Error('Invalid immutable release identity');
  }
  const names = new Set();
  const payloads = files.map(file => {
    if (!/^[A-Za-z0-9][A-Za-z0-9_.+-]{0,255}$/.test(file.name) || names.has(file.name)
        || !Buffer.isBuffer(file.bytes) || file.bytes.length > 128 * 1024 * 1024) {
      throw Error('Invalid bounded release asset');
    }
    names.add(file.name);
    return { ...file, digest: `sha256:${createHash('sha256').update(file.bytes).digest('hex')}` };
  });
  const request = async (path, { method = 'GET', body, upload = false } = {}) => {
    const origin = upload ? 'https://uploads.github.com' : 'https://api.github.com';
    const response = await fetchImpl(`${origin}/repos/${repository}/${path}`, {
      method, redirect: 'error', signal: AbortSignal.timeout(upload ? 60000 : 15000),
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        ...(body ? { 'Content-Type': upload ? 'application/octet-stream' : 'application/json' } : {}) },
      ...(body ? { body: upload ? body : JSON.stringify(body) } : {})
    });
    if (method === 'GET' && response.status === 404) return null;
    if (!response.ok) throw Error(`GitHub release request failed (HTTP ${response.status})`);
    return response.json();
  };
  const readTag = () => request(`commits/${encodeURIComponent(`refs/tags/${tag}`)}`);
  const readRelease = () => request(`releases/tags/${encodeURIComponent(tag)}`);
  let commit = await readTag();
  let release = await readRelease();
  if ((commit && commit.sha !== sha) || (release && !commit)) {
    throw Error('Release tag does not match the qualified source');
  }
  if (!commit) {
    try {
      await request('git/refs', { method: 'POST', body: { ref: `refs/tags/${tag}`, sha } });
    } catch {
      // Creation is reconciled with the actual tag, never force-updated.
    }
    commit = await readTag();
    if (commit?.sha !== sha) throw Error('Release tag creation outcome is unresolved');
  }
  if (!release) {
    try {
      release = await request('releases', { method: 'POST', body: {
        tag_name: tag, target_commitish: sha, name: `Refiner ${tag}`, draft, prerelease,
        body: `Source: ${sha}\nBuild: ${runId} attempt ${attempt}\nImmutable assets; qualification and production activation are separate.`
      } });
    } catch {
      // A lost creation reply must be reconciled before any further write.
      release = await readRelease();
      if (!release) throw Error('Release creation outcome is unresolved');
    }
    commit = await readTag();
  }
  if (commit?.sha !== sha || !Number.isSafeInteger(release?.id) || release.id < 1
      || release.tag_name !== tag || !Array.isArray(release.assets)
      || release.draft !== draft || release.prerelease !== prerelease) {
    throw Error('Release source or publication state cannot be verified');
  }
  // Check every existing identity before making any upload. Never clobber/delete.
  for (const file of payloads) {
    const matches = release.assets.filter(asset => asset.name === file.name);
    if (matches.length > 1 || (matches.length && matches[0].digest !== file.digest)) {
      throw Error('Existing immutable release asset differs');
    }
  }
  for (const file of payloads) {
    if (release.assets.some(asset => asset.name === file.name)) continue;
    let asset;
    try {
      asset = await request(`releases/${release.id}/assets?name=${encodeURIComponent(file.name)}`,
        { method: 'POST', upload: true, body: file.bytes });
    } catch {
      const observed = await readRelease();
      asset = observed?.assets?.find(asset => asset.name === file.name);
    }
    if (asset?.name !== file.name || asset.digest !== file.digest) {
      throw Error('Asset publication outcome is unresolved');
    }
  }
  const finalRelease = await readRelease();
  const finalCommit = await readTag();
  if (finalCommit?.sha !== sha || finalRelease?.id !== release.id
      || finalRelease.draft !== draft || finalRelease.prerelease !== prerelease
      || !payloads.every(file => finalRelease.assets?.filter(asset =>
        asset.name === file.name && asset.digest === file.digest).length === 1)) {
    throw Error('Final release identity cannot be verified');
  }
  return { tag, sha, assets: payloads.map(({ name, digest }) => ({ name, digest })) };
}

export async function resolveImageDigest({ image, sha, username, password,
  sourceRepository = 'neuralmimicry/rag_demo', expectedArchitecture, fetchImpl = fetch }) {
  const identity = /^ghcr\.io\/([a-z0-9_.-]+\/[a-z0-9_.-]+):([A-Za-z0-9_.-]{1,128})$/.exec(image);
  if (!identity || !/^[a-f0-9]{40}$/.test(sha) || !username || !password
      || !/^[\w.-]+\/[\w.-]+$/.test(sourceRepository)) {
    throw Error('Invalid image publication identity');
  }
  const [, repository, tag] = identity;
  const authorization = `Basic ${Buffer.from(`${username}:${password}`).toString('base64')}`;
  const auth = await fetchImpl(`https://ghcr.io/token?service=ghcr.io&scope=${encodeURIComponent(`repository:${repository}:pull`)}`,
    { headers: { Authorization: authorization }, redirect: 'error', signal: AbortSignal.timeout(15000) });
  if (!auth.ok) throw Error('Registry credential scope cannot be verified');
  const token = (await auth.json()).token;
  if (typeof token !== 'string' || token.length < 10) throw Error('Registry observation token is unavailable');
  const read = async (path, digest) => {
    const blob = path.startsWith('blobs/');
    let response = await fetchImpl(`https://ghcr.io/v2/${repository}/${path}`, {
      headers: { Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' },
      redirect: blob ? 'manual' : 'error', signal: AbortSignal.timeout(15000) });
    if (blob && [302, 307].includes(response.status)) {
      const destination = new URL(response.headers.get('location'));
      if (destination.protocol !== 'https:' || destination.hostname !== 'pkg-containers.githubusercontent.com'
          || destination.port || destination.username || destination.password || destination.hash) {
        throw Error('Registry blob redirect is not trusted');
      }
      // GHCR serves signed blobs through this CDN. Never forward the bearer token.
      response = await fetchImpl(destination.href,
        { redirect: 'error', signal: AbortSignal.timeout(15000) });
    }
    if (!response.ok) throw Error('Registry image observation failed');
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > 1024 * 1024) throw Error('Registry metadata exceeds bounds');
      chunks.push(chunk);
    }
    const bytes = Buffer.concat(chunks);
    const actual = `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
    if ((digest && actual !== digest) || (!digest && response.headers.get('docker-content-digest') !== actual)) {
      throw Error('Registry content digest does not match');
    }
    return { digest: actual, value: JSON.parse(bytes.toString('utf8')) };
  };
  const manifest = await read(`manifests/${tag}`);
  const verifyManifest = async (value, architecture) => {
    if (!/^sha256:[a-f0-9]{64}$/.test(value.config?.digest)) throw Error('Image config identity is missing');
    const config = await read(`blobs/${value.config.digest}`, value.config.digest);
    if (!['amd64', 'arm64'].includes(architecture) || config.value.os !== 'linux'
        || config.value.architecture !== architecture
        || config.value.config?.Labels?.['org.opencontainers.image.revision'] !== sha
        || config.value.config?.Labels?.['org.opencontainers.image.source'] !== `https://github.com/${sourceRepository}`) {
      throw Error('Image config does not match the qualified source');
    }
  };
  if (Array.isArray(manifest.value.manifests)) {
    const variants = manifest.value.manifests;
    if (variants.length !== 2 || !['amd64', 'arm64'].every(arch =>
      variants.filter(v => v.platform?.os === 'linux' && v.platform.architecture === arch).length === 1)) {
      throw Error('Required native image architectures are missing');
    }
    for (const variant of variants) {
      if (!/^sha256:[a-f0-9]{64}$/.test(variant.digest)) throw Error('Invalid architecture digest');
      await verifyManifest((await read(`manifests/${variant.digest}`, variant.digest)).value, variant.platform.architecture);
    }
  } else {
    await verifyManifest(manifest.value, expectedArchitecture);
  }
  return manifest.digest;
}

async function main() {
  if (process.argv[2] === '--image') {
    const digest = await resolveImageDigest({ image: process.env.IMAGE_REF, sha: process.env.GITHUB_SHA,
      username: process.env.GHCR_USERNAME, password: process.env.GHCR_PASSWORD,
      sourceRepository: process.env.GITHUB_REPOSITORY,
      expectedArchitecture: process.env.IMAGE_PLATFORM?.split('/')[1] });
    if ((await readFile(process.env.IMAGE_DIGEST_FILE, 'utf8')).trim() !== digest) {
      throw Error('Pushed and independently observed image digests differ');
    }
    const { appendFile } = await import('node:fs/promises');
    await appendFile(process.env.GITHUB_OUTPUT, `digest=${digest}\n`);
    console.log(`Verified source-bound registry digest ${digest}`);
    return;
  }
  const directory = resolve('dist');
  const files = [];
  for (const name of await readdir(directory)) {
    const path = resolve(directory, name);
    const info = await stat(path);
    if (!info.isFile() || info.size > 128 * 1024 * 1024) throw Error('Invalid release payload');
    files.push({ name, bytes: await readFile(path) });
  }
  const result = await publishRelease({ repository: process.env.GITHUB_REPOSITORY,
    sha: process.env.GITHUB_SHA, tag: process.env.RELEASE_TAG, token: process.env.GH_TOKEN,
    files, draft: process.env.DRAFT_RELEASE === 'true',
    prerelease: process.env.MANUAL_PRERELEASE === 'true' || process.env.RELEASE_TAG.includes('-'),
    runId: process.env.GITHUB_RUN_ID, attempt: process.env.GITHUB_RUN_ATTEMPT });
  console.log(JSON.stringify(result));
}
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch(() => { console.error('Immutable release publication failed; inspect source and asset identities.'); process.exitCode = 1; });
}
