<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { tick } from 'svelte';
	import Cover from './Cover.svelte';
	import Glyph from './Glyph.svelte';
	import Mosaic from './Mosaic.svelte';
	import { app } from '#lib/state.svelte.ts';
	import { fadeCovers, fadeStatus } from './fade.ts';
	import { Spring } from './motion.ts';
	import { tintVars } from './tint.ts';
	import FadeBadge from './FadeBadge.svelte';
	import { t } from './i18n.svelte.ts';
	import { ui, type Ctx, type CtxItem } from './ui.svelte.ts';

	// The pressed thing lifts out of the page and grows into a preview; the menu unfolds below it.
	// Closing sends the preview back to exactly where it came from.
	let cur = $state<Ctx | null>(null);
	let p = $state(0);
	let menuEl: HTMLElement | undefined = $state();
	let geo = $state({ W: 260, H: 320, left: 0, top: 0, menuTop: 0, menuLeft: 0, menuW: 250, dx: 0, dy: 0, s0: 1 });
	const spring = new Spring(0, (v) => (p = v), 0.001);

	$effect(() => {
		const c = ui.ctx;
		if (c && c !== cur) show(c);
	});

	async function show(c: Ctx) {
		cur = c;
		const vw = window.innerWidth;
		const vh = window.innerHeight;
		const phone = vw <= 720;
		const W = c.preview.kind === 'fade' ? Math.min(phone ? 240 : 260, vw - 80) : Math.min(phone ? 220 : 240, vw - 80);
		const H = W + 74;
		const menuW = Math.min(phone ? 300 : 280, vw - 32);
		const menuH = c.items.length * 50 + 12;
		const gap = 12;
		let left: number;
		let top: number;
		if (phone) {
			left = (vw - W) / 2;
			top = Math.max(56, Math.min((vh - H - gap - menuH) / 2, vh - H - gap - menuH - 24));
		} else {
			left = Math.max(16, Math.min(c.rect.left, vw - Math.max(W, menuW) - 16));
			top = Math.max(16, Math.min(c.rect.top, vh - H - gap - menuH - 16));
		}
		const menuLeft = phone ? (vw - menuW) / 2 : left;
		// FLIP: start where the pressed element is, at its size
		const s0 = Math.max(0.2, Math.min(1, c.rect.width / W));
		const dx = c.rect.left + c.rect.width / 2 - (left + W / 2);
		const dy = c.rect.top + c.rect.height / 2 - (top + H / 2);
		geo = { W, H, left, top, menuTop: top + H + gap, menuLeft, menuW, dx, dy, s0 };
		spring.set(0);
		await tick();
		spring.to(1, { response: 0.42, damping: 0.8 });
		menuEl?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true });
	}

	async function close(then?: () => void) {
		if (!cur) return;
		const el = cur.el;
		const done = await spring.to(0, { response: 0.32, damping: 1 });
		if (!done) return;
		cur = null;
		ui.ctx = null;
		el.focus?.({ preventScroll: true });
		then?.();
	}
	function choose(it: CtxItem) {
		// the action happens as the preview returns, not after
		it.run();
		close();
	}
	function key(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.stopPropagation();
			close();
			return;
		}
		const d = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
		if (!d || !menuEl) return;
		e.preventDefault();
		const b = [...menuEl.querySelectorAll<HTMLElement>('button')];
		b[(b.indexOf(document.activeElement as HTMLElement) + d + b.length) % b.length]?.focus();
	}

	const fade = $derived(cur?.preview.kind === 'fade' ? app.line(cur.preview.id) : null);
	const tr = $derived(cur?.preview.kind === 'track' ? cur.preview.track : null);
</script>

{#if cur}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="ctx" style:--p={p} onkeydown={key}>
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<div class="scrim" onclick={() => close()}></div>
		<div
			class="preview glass strong"
			style:left="{geo.left}px"
			style:top="{geo.top}px"
			style:width="{geo.W}px"
			style:transform="translate({geo.dx * (1 - p)}px, {geo.dy * (1 - p)}px) scale({geo.s0 + (1 - geo.s0) * p})"
			aria-hidden="true"
		>
			{#if fade}
				<div class="tinted" style={tintVars(fade.color)}>
					<Mosaic covers={fadeCovers(fade)} seed={fade.name} color={fade.color} size="100%" />
					<p class="pv-t clip"><FadeBadge icon={fade.icon} color={fade.color} size={20} /> {fade.name}</p>
					<p class="pv-s t-foot t-2 clip">{fadeStatus(fade).text}</p>
				</div>
			{:else if tr}
				<Cover src={tr.artwork} seed={tr.id} size="100%" radius={0.1} track={tr} />
				<p class="pv-t clip">{tr.title}</p>
				<p class="pv-s t-foot t-2 clip">
					{tr.artist}
					{#if tr.service}<span class="marks"><ServiceMark service={tr.service} size={13} title={false} /></span>{/if}
				</p>
			{/if}
		</div>
		<div
			bind:this={menuEl}
			class="menu glass strong"
			role="menu"
			aria-label={t('ctx.actionsFor', { name: fade?.name ?? tr?.title ?? '' })}
			style:left="{geo.menuLeft}px"
			style:top="{geo.menuTop}px"
			style:width="{geo.menuW}px"
		>
			{#each cur.items as it}
				<button role="menuitem" class="item" class:destructive={it.role === 'destructive'} onclick={() => choose(it)}>
					<span class="clip">{it.label}</span>
					<Glyph name={it.icon} size={20} weight={2} />
				</button>
			{/each}
		</div>
		<span class="sr" aria-live="polite">{tr && tr.service ? t('ctx.available', { title: tr.title, services: SERVICE_NAME[tr.service] }) : ''}</span>
	</div>
{/if}

<style>
	.ctx {
		position: fixed;
		inset: 0;
		z-index: 420;
	}
	.scrim {
		position: absolute;
		inset: 0;
		background: rgb(0 0 0 / calc(var(--p) * 0.16));
		-webkit-backdrop-filter: blur(calc(var(--p) * 14px)) saturate(calc(1 + var(--p) * 0.3));
		backdrop-filter: blur(calc(var(--p) * 14px)) saturate(calc(1 + var(--p) * 0.3));
	}
	.preview {
		position: fixed;
		padding: 10px 10px 12px;
		border-radius: 30px;
		transform-origin: 50% 50%;
		box-shadow: 0 24px 60px -18px rgb(0 0 0 / 0.45);
	}
	.pv-t {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 10px 4px 0;
		font-weight: 700;
	}
	.pv-s {
		display: flex;
		align-items: center;
		gap: 6px;
		margin: 1px 4px 0;
	}
	.marks {
		display: inline-flex;
		gap: 3px;
	}
	.menu {
		position: fixed;
		padding: 6px;
		border-radius: 26px;
		transform-origin: 50% 0;
		opacity: min(1, calc(var(--p) * 1.6));
		transform: translateY(calc((1 - var(--p)) * -16px)) scale(calc(0.6 + var(--p) * 0.4));
	}
	.item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		width: 100%;
		min-height: 50px;
		padding: 0 14px 0 16px;
		border-radius: 20px;
		font-size: var(--f-body);
		text-align: left;
	}
	.item:hover,
	.item:focus-visible {
		background: var(--fill-3);
		outline: none;
	}
	.item:active {
		background: var(--fill-2);
	}
	.destructive {
		color: var(--red-text);
	}
	@media (prefers-reduced-motion: reduce) {
		.preview {
			transform: none !important;
			opacity: var(--p);
		}
		.menu {
			transform: none;
		}
	}
</style>
