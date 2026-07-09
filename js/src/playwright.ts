/**
 * Playwright launch wrapper for cloakbrowser.
 * Mirrors Python cloakbrowser/browser.py.
 */

import type { Browser, BrowserContext, BrowserContextOptions, LaunchOptions as PlaywrightLaunchOptions } from "playwright-core";
import type { LaunchOptions, LaunchContextOptions, LaunchPersistentContextOptions } from "./types.js";
import { DEFAULT_VIEWPORT, IGNORE_DEFAULT_ARGS } from "./config.js";
import { buildArgs } from "./args.js";
import { ensureBinary } from "./download.js";
import { resolveProxyConfig } from "./proxy.js";
import { maybeResolveGeoip, resolveWebrtcArgs } from "./geoip.js";
import { seedWidevineHint } from "./widevine.js";

/** @internal Accept both timezone and timezoneId — either works, no warning. Exported for testing. */
export function resolveTimezone<T extends { timezone?: string; timezoneId?: string }>(options: T): T {
  if (options.timezoneId != null) {
    const merged = { ...options, timezone: options.timezone ?? options.timezoneId };
    delete (merged as any).timezoneId;
    return merged;
  }
  return options;
}

/**
 * Strip `locale` and `timezoneId` from user-provided contextOptions — both route
 * through detectable CDP emulation. The wrapper's top-level `locale`/`timezone`
 * fields use binary flags instead (undetectable). Warn so users notice.
 */
function filterStealthCtxOptions(ctx?: BrowserContextOptions): Partial<BrowserContextOptions> {
  if (!ctx) return {};
  const { locale, timezoneId, ...rest } = ctx;
  if (locale !== undefined) {
    console.warn(
      "[cloakbrowser] contextOptions.locale ignored — use top-level `locale` " +
      "instead (routes through binary flag, avoids detectable CDP emulation)."
    );
  }
  if (timezoneId !== undefined) {
    console.warn(
      "[cloakbrowser] contextOptions.timezoneId ignored — use top-level `timezone` " +
      "instead (routes through binary flag, avoids detectable CDP emulation)."
    );
  }
  return rest;
}

/**
 * Build Playwright BrowserContext options for CloakBrowser without launching a browser
 * or creating a context.
 *
 * Useful when integrating CloakBrowser with an existing Playwright Browser while
 * keeping the wrapper's stealth-safe defaults for `newContext()`.
 */
export function buildContextOptions(
  options: LaunchContextOptions = {}
): BrowserContextOptions {
  return {
    // contextOptions first — explicit wrapper fields below override it.
    // filterStealthCtxOptions strips locale/timezoneId to prevent CDP detection.
    ...filterStealthCtxOptions(options.contextOptions),
    ...(options.userAgent ? { userAgent: options.userAgent } : {}),
    viewport: options.viewport === undefined ? DEFAULT_VIEWPORT : options.viewport,
    ...(options.colorScheme ? { colorScheme: options.colorScheme } : {}),
  } as BrowserContextOptions;
}

/**
 * Resolve geoip (timezone/locale/exitIp) and WebRTC args in a single pass.
 *
 * `resolveWebrtcArgs` performs its own proxy exit-IP probe, and so does
 * `maybeResolveGeoip`. Calling both independently double-probes the network.
 * This helper resolves geoip first, then reuses the exit IP for the WebRTC
 * flag instead of probing again. When an exit IP is already known (e.g. the
 * caller resolved it upstream, as `launchContext` does), pass `knownExitIp`
 * to skip both probes entirely.
 *
 * Returns the merged `args`, the non-geo `LaunchOptions` spread (`resolved`),
 * and the resolved `exitIp` for reuse by the caller.
 */
async function resolveWebrtcAndGeoArgs(
  options: LaunchOptions,
  knownExitIp?: string,
): Promise<{ args: string[] | undefined; resolved: Omit<LaunchOptions, "args">; exitIp?: string }> {
  // geoip: false (or no proxy) short-circuits — maybeResolveGeoip returns
  // { timezone, locale } without any network probe when geoip is off.
  const { exitIp: geoExitIp, ...resolved } = await maybeResolveGeoip(options);
  const exitIp = knownExitIp ?? geoExitIp;

  // If we already have an exit IP, splice it into args WITHOUT re-probing.
  // resolveWebrtcArgs would otherwise re-run resolveExitIp over the network.
  const args = options.args ? [...options.args] : undefined;
  if (args) {
    const idx = args.findIndex(a => a === "--fingerprint-webrtc-ip=auto");
    if (idx !== -1) {
      if (exitIp) {
        args[idx] = `--fingerprint-webrtc-ip=${exitIp}`;
      } else {
        // No exit IP resolvable — drop the auto flag (matches resolveWebrtcArgs behavior).
        args.splice(idx, 1);
      }
    }
  }
  if (exitIp && !(args ?? []).some(a => a.startsWith("--fingerprint-webrtc-ip"))) {
    args?.push(`--fingerprint-webrtc-ip=${exitIp}`);
  }

  return { args, resolved: resolved as Omit<LaunchOptions, "args">, exitIp };
}

/**
 * Build Playwright launch options for CloakBrowser without starting Chromium.
 *
 * Useful when integrating CloakBrowser with a custom Playwright build or another
 * wrapper that needs to call `chromium.launch()` itself.
 */
export async function buildLaunchOptions(
  options: LaunchOptions = {},
  /** @internal Pre-resolved exit IP — skips the WebRTC/geoip probe. */
  knownExitIp?: string,
): Promise<PlaywrightLaunchOptions> {
  const binaryPath = process.env.CLOAKBROWSER_BINARY_PATH || (await ensureBinary());
  const { args: resolvedArgs, resolved } = await resolveWebrtcAndGeoArgs(options, knownExitIp);
  const { proxyOption, proxyArgs } = resolveProxyConfig(options.proxy);
  const args = buildArgs({ ...options, ...resolved, args: [...(resolvedArgs ?? []), ...proxyArgs] });

  return {
    executablePath: binaryPath,
    headless: options.headless ?? true,
    args,
    ignoreDefaultArgs: IGNORE_DEFAULT_ARGS,
    ...(proxyOption ? { proxy: proxyOption } : {}),
    ...options.launchOptions,
  } as PlaywrightLaunchOptions;
}

/**
 * Apply CloakBrowser's human-like behavioral layer to an existing Playwright browser.
 */
export async function humanizeBrowser(
  browser: Browser,
  options: LaunchOptions = {}
): Promise<void> {
  if (!options.humanize) return;

  const { patchBrowser } = await import('./human/index.js');
  const { resolveConfig } = await import('./human/config.js');
  const cfg = resolveConfig(
    options.humanPreset ?? 'default',
    options.humanConfig,
  );
  patchBrowser(browser, cfg);
}

/**
 * Launch stealth Chromium browser via Playwright.
 *
 * @example
 * ```ts
 * import { launch } from 'cloakbrowser';
 * const browser = await launch();
 * const page = await browser.newPage();
 * await page.goto('https://bot.incolumitas.com');
 * console.log(await page.title());
 * await browser.close();
 * ```
 */
export async function launch(
  options: LaunchOptions = {},
  /** @internal Pre-resolved exit IP — skips the WebRTC/geoip probe. */
  knownExitIp?: string,
): Promise<Browser> {
  const { chromium } = await import("playwright-core");
  // Resolve geoip + WebRTC once here; pass the result into buildLaunchOptions
  // so it doesn't re-probe the network for the exit IP.
  const { exitIp, resolved: preResolved, args: preArgs } = await resolveWebrtcAndGeoArgs(options, knownExitIp);
  const launchOpts = await buildLaunchOptions({ ...options, ...preResolved, args: preArgs }, exitIp);
  const browser = await chromium.launch(launchOpts);
  await humanizeBrowser(browser, options);
  return browser;
}

/**
 * Launch stealth browser and return a BrowserContext with common options pre-set.
 * Closing the context also closes the browser.
 *
 * @example
 * ```ts
 * import { launchContext } from 'cloakbrowser';
 * const context = await launchContext({
 *   userAgent: 'Mozilla/5.0...',
 *   viewport: { width: 1920, height: 1080 },
 * });
 * const page = await context.newPage();
 * await page.goto('https://example.com');
 * await context.close(); // also closes browser
 * ```
 */
export async function launchContext(
  options: LaunchContextOptions = {}
): Promise<BrowserContext> {
  options = resolveTimezone(options);
  // Resolve geoip + WebRTC args ONCE here, then hand the exit IP to launch()
  // so buildLaunchOptions() doesn't re-probe the proxy for the exit IP.
  const { args: resolvedArgs, resolved, exitIp } = await resolveWebrtcAndGeoArgs(options);
  // --fingerprint-timezone is process-wide (reads CommandLine in renderer),
  // so it applies to ALL contexts, not just the default one.
  // locale and timezone are set via binary flags only — no CDP emulation.
  const browser = await launch({ ...options, ...resolved, args: resolvedArgs, geoip: false }, exitIp);

  let context: BrowserContext;
  try {
    context = await browser.newContext(buildContextOptions(options));
  } catch (err) {
    await browser.close();
    throw err;
  }

  // Patch close() to also close the browser
  const origClose = context.close.bind(context);
  context.close = async () => {
    await origClose();
    await browser.close();
  };

  // Human-like behavioral patching
  if (options.humanize) {
    const { patchContext } = await import('./human/index.js');
    const { resolveConfig } = await import('./human/config.js');
    const cfg = resolveConfig(
      options.humanPreset ?? 'default',
      options.humanConfig,
    );
    patchContext(context, cfg);
  }

  return context;
}

/**
 * Launch stealth browser with a persistent user profile (non-incognito).
 * Uses Playwright's chromium.launchPersistentContext() under the hood.
 *
 * This avoids incognito detection by services like BrowserScan (-10% penalty)
 * and enables session persistence (cookies, localStorage) across launches.
 *
 * @example
 * ```ts
 * import { launchPersistentContext } from 'cloakbrowser';
 * const context = await launchPersistentContext({
 *   userDataDir: './chrome-profile',
 *   headless: false,
 *   proxy: 'http://user:pass@host:port',
 *   geoip: true,
 * });
 * const page = context.pages()[0] || await context.newPage();
 * await page.goto('https://example.com');
 * await context.close();
 * ```
 */
export async function launchPersistentContext(
  options: LaunchPersistentContextOptions
): Promise<BrowserContext> {
  options = resolveTimezone(options);
  const { chromium } = await import("playwright-core");

  const binaryPath = process.env.CLOAKBROWSER_BINARY_PATH || (await ensureBinary());
  const { exitIp, ...resolved } = await maybeResolveGeoip(options);
  const { proxyOption, proxyArgs } = resolveProxyConfig(options.proxy);
  let resolvedArgs = await resolveWebrtcArgs(options);
  if (exitIp && !(resolvedArgs ?? []).some(a => a.startsWith("--fingerprint-webrtc-ip"))) {
    resolvedArgs = [...(resolvedArgs ?? []), `--fingerprint-webrtc-ip=${exitIp}`];
  }
  const args = buildArgs({ ...options, ...resolved, args: [...(resolvedArgs ?? []), ...proxyArgs] });

  seedWidevineHint(options.userDataDir, binaryPath);

  // locale and timezone are set via binary flags (--lang, --fingerprint-timezone)
  // — NOT via Playwright context kwargs which use detectable CDP emulation.
  const context = await chromium.launchPersistentContext(options.userDataDir, {
    executablePath: binaryPath,
    headless: options.headless ?? true,
    args,
    ignoreDefaultArgs: IGNORE_DEFAULT_ARGS,
    ...(proxyOption ? { proxy: proxyOption } : {}),
    ...buildContextOptions(options),
    ...options.launchOptions,
  });

  // Human-like behavioral patching
  if (options.humanize) {
    const { patchContext } = await import('./human/index.js');
    const { resolveConfig } = await import('./human/config.js');
    const cfg = resolveConfig(
      options.humanPreset ?? 'default',
      options.humanConfig,
    );
    patchContext(context, cfg);
  }

  return context;
}

// ---------------------------------------------------------------------------
// Internal
// ---------------------------------------------------------------------------

/** @internal Exposed for unit tests only. */
export { buildArgs as _buildArgsForTest } from "./args.js";
