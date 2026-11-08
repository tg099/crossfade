<script lang="ts" module>
	let counter = 0;
</script>

<script lang="ts">
	import { onDestroy, tick, untrack, type Snippet } from 'svelte';
	import { project, reducedMotion, rubberband, Spring, VelocityTracker } from './motion.ts';
	import { ui } from './ui.svelte.ts';

	// A sheet that behaves like a physical card: it tracks the finger 1:1 (header anywhere, or the
	// content once it is scrolled to the top), throws by momentum, resists upward, and can be
	// caught mid-flight. Phones: page sheet that pushes the page behind back. Desktop: form sheet.
	let {
		open = $bindable(false),
		label,
		class: cls = '',
		style = '',
		onclosed,
		header,
		children
	}: {
		open?: boolean;
		label: string;
		class?: string;
		style?: string;
		onclosed?: () => void;
		header?: Snippet;
		children?: Snippet;
	} = $props();

	const id = ++counter;
	let mounted = $state(false);
	let leaving = $state(false);
	let y = $state(0);
	let H = 800;
	let shown = false;
	let sheetEl: HTMLElement | undefined = $state();
	let bodyEl: HTMLElement | undefined = $state();
	let restoreFocus: HTMLElement | null = null;

	const spring = new Spring(0, (v) => {
		y = v;
		const e = ui.sheets.find((s) => s.id === id);
		if (e) e.p = Math.min(1, Math.max(0, 1 - v / H));
	});

	const index = $derived(ui.sheets.findIndex((s) => s.id === id));
	const above = $derived(index >= 0 ? (ui.sheets[index + 1]?.p ?? 0) : 0);
	const p = $derived(Math.min(1, Math.max(0, 1 - y / H)));
	const isTop = $derived(index === ui.sheets.length - 1);
	// Desktop sheets fit their content. A sheet opened from another one is at least as tall as
	// that one, so the sheet underneath never peeks out above and below it.
	let ownH = $state(0);
	$effect(() => {
		const h = ownH;
		untrack(() => {
			const e = ui.sheets.find((s) => s.id === id);
			if (e && e.h !== h) e.h = h;
		});
	});
	const floorH = $derived(index > 0 ? (ui.sheets[index - 1]?.h ?? 0) : 0);

	$effect(() => {
		const o = open;
		untrack(() => {
			if (o && !shown) present();
			else if (!o && shown) dismiss();
		});
	});

	async function present(v = 0) {
		shown = true;
		open = true;
		leaving = false;
		if (!mounted) {
			H = window.innerHeight;
			restoreFocus = document.activeElement as HTMLElement | null;
			spring.set(reducedMotion() ? 0 : H);
			ui.sheets.push({ id, p: reducedMotion() ? 1 : 0 });
			mounted = true;
			await tick();
			sheetEl?.focus({ preventScroll: true });
		}
		spring.to(0, { damping: v < -300 ? 0.86 : 1, response: 0.42, velocity: v });
	}

	function dismiss(v = 0) {
		shown = false;
		open = false;
		if (reducedMotion()) {
			leaving = true;
			setTimeout(finish, 200);
			return;
		}
		spring.to(H, { damping: 1, response: 0.36, velocity: Math.max(v, 0) }).then((done) => done && finish());
	}

	function finish() {
		if (shown) return;
		leaving = false;
		mounted = false;
		const i = ui.sheets.findIndex((s) => s.id === id);
		if (i >= 0) ui.sheets.splice(i, 1);
		restoreFocus?.focus?.({ preventScroll: true });
		onclosed?.();
	}

	onDestroy(() => {
		spring.stop();
		const i = ui.sheets.findIndex((s) => s.id === id);
		if (i >= 0) ui.sheets.splice(i, 1);
	});

	// ------------------------------------------------------------ dragging
	const tracker = new VelocityTracker();
	let from: { y0: number; v0: number } | null = null;

	function begin(clientY: number) {
		spring.stop();
		from = { y0: clientY, v0: y };
		tracker.reset();
		tracker.add(0, clientY);
		if (!shown) {
			// caught while closing: it's ours again
			shown = true;
			open = true;
		}
	}
	function move(clientY: number) {
		if (!from) return;
		const d = from.v0 + clientY - from.y0;
		spring.set(d < 0 ? -rubberband(-d, H) : d);
		tracker.add(0, clientY);
	}
	function end() {
		if (!from) return;
		from = null;
		const v = tracker.get().y;
		const close = Math.abs(v) > 500 ? v > 0 : y + project(v) > H * 0.4;
		if (close) dismiss(v);
		else present(v);
	}

	// header: pointer events (mouse + touch). Followed window-wide from the press, so a fast drag
	// can't outrun the header; it only becomes a drag after a few px, so taps on header buttons stay taps.
	let pending: { id: number; y: number } | null = null;
	function hdown(e: PointerEvent) {
		if (e.button !== 0) return;
		pending = { id: e.pointerId, y: e.clientY };
		window.addEventListener('pointermove', hmove);
		window.addEventListener('pointerup', hup);
		window.addEventListener('pointercancel', hup);
	}
	function hmove(e: PointerEvent) {
		if (pending && pending.id === e.pointerId && Math.abs(e.clientY - pending.y) > 4) {
			begin(pending.y);
			pending = null;
		}
		if (from) {
			e.preventDefault();
			move(e.clientY);
		}
	}
	function hup() {
		window.removeEventListener('pointermove', hmove);
		window.removeEventListener('pointerup', hup);
		window.removeEventListener('pointercancel', hup);
		pending = null;
		end();
	}
	onDestroy(() => {
		window.removeEventListener('pointermove', hmove);
		window.removeEventListener('pointerup', hup);
		window.removeEventListener('pointercancel', hup);
	});

	// content: take over from scrolling only when it is at the top and the finger moves down
	let t: { x: number; y: number; atTop: boolean; decided: boolean; active: boolean } | null = null;
	function tstart(e: TouchEvent) {
		if (e.touches.length !== 1 || !bodyEl) return;
		const p0 = e.touches[0];
		t = { x: p0.clientX, y: p0.clientY, atTop: bodyEl.scrollTop <= 0, decided: false, active: false };
	}
	function tmove(e: TouchEvent) {
		if (!t) return;
		const p0 = e.touches[0];
		const dx = p0.clientX - t.x;
		const dy = p0.clientY - t.y;
		if (!t.decided) {
			if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
			t.decided = true;
			t.active = t.atTop && dy > 0 && Math.abs(dy) > Math.abs(dx);
			if (t.active) begin(t.y);
		}
		if (t.active) {
			e.preventDefault();
			move(p0.clientY);
		}
	}
	function tend() {
		if (t?.active) end();
		t = null;
	}
	$effect(() => {
		const el = bodyEl;
		if (!el) return;
		el.addEventListener('touchmove', tmove, { passive: false });
		return () => el.removeEventListener('touchmove', tmove);
	});

	// sheets live above everything: never inside the page they push back (or make inert)
	function portal(node: HTMLElement) {
		document.body.appendChild(node);
		return { destroy: () => node.remove() };
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && mounted && shown && isTop) {
			e.stopPropagation();
			dismiss();
		}
	}
</script>

<svelte:window {onkeydown} />

{#if mounted}
	<div class="layer" use:portal class:leaving class:rm={reducedMotion()} style:z-index={200 + index * 2}>
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
		<div class="scrim" style:opacity={p * (index === 0 ? 0.32 : 0.18)} onclick={() => dismiss()}></div>
		<div
			bind:this={sheetEl}
			class="sheet {cls}"
			role="dialog"
			aria-modal="true"
			aria-label={label}
			tabindex="-1"
			inert={above > 0.02}
			bind:offsetHeight={ownH}
			style="--y:{y}px; --above:{above}; --floor:{floorH}px; {style}"
		>
			<!-- svelte-ignore a11y_no_static_element_interactions (drag handle; Escape and the buttons inside close it too) -->
			<header class="head" onpointerdown={hdown}>
				<span class="grabber" aria-hidden="true"></span>
				{@render header?.()}
			</header>
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="body" bind:this={bodyEl} ontouchstart={tstart} ontouchend={tend} ontouchcancel={tend}>
				{@render children?.()}
			</div>
			<span class="push-dim" style:opacity={above * 0.25} aria-hidden="true"></span>
		</div>
	</div>
{/if}

<style>
	.layer {
		position: fixed;
		inset: 0;
	}
	.scrim {
		position: absolute;
		inset: 0;
		background: var(--scrim);
	}
	.sheet {
		position: absolute;
		/* floats inside the screen, corners concentric with the device */
		left: 8px;
		right: 8px;
		bottom: max(8px, var(--safe-bottom));
		top: calc(var(--safe-top) + 10px);
		display: flex;
		flex-direction: column;
		background: var(--sheet);
		-webkit-backdrop-filter: blur(40px) saturate(1.8);
		backdrop-filter: blur(40px) saturate(1.8);
		color: var(--label);
		--cell-grouped: var(--sheet-cell);
		border-radius: 38px;
		box-shadow:
			var(--shadow-sheet),
			var(--glass-depth),
			inset 0 0 0 0.5px var(--rim-edge);
		overflow: hidden;
		isolation: isolate;
		outline: none;
		transform-origin: 50% 0;
		transform: translate3d(0, var(--y), 0) scale(calc(1 - var(--above) * 0.06));
		will-change: transform;
	}
	/* same glass as everywhere else: a sheen from above and a rim that catches the light */
	.sheet::before {
		content: '';
		position: absolute;
		inset: 0;
		z-index: -1;
		border-radius: inherit;
		background: radial-gradient(130% 60% at 50% -20%, var(--sheen), transparent 62%);
		pointer-events: none;
	}
	.sheet::after {
		content: '';
		position: absolute;
		inset: 0;
		z-index: 1;
		border-radius: inherit;
		padding: var(--rim-w, 1px);
		background: linear-gradient(150deg, var(--rim-hi), var(--rim-mid, transparent) 30%, var(--rim-mid, transparent) 70%, var(--rim-lo));
		-webkit-mask:
			linear-gradient(#000 0 0) content-box,
			linear-gradient(#000 0 0);
		mask:
			linear-gradient(#000 0 0) content-box,
			linear-gradient(#000 0 0);
		-webkit-mask-composite: xor;
		mask-composite: exclude;
		pointer-events: none;
	}
	@media (min-width: 721px) {
		.sheet {
			top: 50%;
			left: 50%;
			right: auto;
			bottom: auto;
			width: min(620px, 100vw - 48px);
			height: auto;
			min-height: min(var(--floor, 0px), 100dvh - 64px);
			max-height: min(780px, 100dvh - 64px);
			border-radius: 30px;
			transform-origin: 50% 50%;
			transform: translate3d(-50%, calc(-50% + var(--y)), 0) scale(calc(1 - var(--above) * 0.04));
		}
	}
	.head {
		position: relative;
		flex: none;
		touch-action: none;
		padding-top: 7px;
		user-select: none;
		-webkit-user-select: none;
	}
	.grabber {
		display: block;
		width: 38px;
		height: 5px;
		margin: 0 auto 4px;
		border-radius: 3px;
		background: var(--label-3);
	}
	.body {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		-webkit-overflow-scrolling: touch;
	}
	.push-dim {
		position: absolute;
		inset: 0;
		background: #000;
		pointer-events: none;
	}

	/* reduced motion: a calm cross-fade instead of travel */
	.rm .sheet {
		animation: fade 200ms ease both;
	}
	.rm .scrim {
		animation: fade 200ms ease both;
	}
	.rm.leaving {
		opacity: 0;
		transition: opacity 180ms ease;
	}
	@keyframes fade {
		from {
			opacity: 0;
		}
	}
</style>
