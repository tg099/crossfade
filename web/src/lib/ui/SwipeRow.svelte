<script lang="ts" module>
	export interface SwipeAction {
		label: string;
		tone: 'red' | 'gray' | 'orange' | 'tint';
		run: () => void;
	}
	// only one row is open at a time
	let closeOpen: (() => void) | null = null;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import { t } from './i18n.svelte.ts';
	import { gesture, project, rubberband, Spring, tick10, VelocityTracker } from './motion.ts';

	// Swipe left to reveal actions; swipe far to run the first one straight away.
	let { actions, children }: { actions: SwipeAction[]; children: Snippet } = $props();

	const token = Symbol('swipe');
	const ACTION_W = 84;
	let x = $state(0);
	let width = $state(0);
	let committing = $state(false);
	const spring = new Spring(0, (v) => (x = v), 0.5);
	const tracker = new VelocityTracker();
	const openW = $derived(actions.length * ACTION_W);
	const full = $derived(width * 0.62);
	let armed = false;

	let start: { x: number; y: number; id: number; x0: number } | null = null;
	let locked = false;
	let suppressClick = false;

	let root: HTMLElement | undefined = $state();

	function close() {
		spring.to(0, { response: 0.34 });
		if (closeOpen === close) closeOpen = null;
	}
	function open(velocity = 0) {
		spring.to(-openW, { response: 0.34, velocity, damping: Math.abs(velocity) > 600 ? 0.85 : 1 });
		if (closeOpen && closeOpen !== close) closeOpen();
		closeOpen = close;
		// like iOS: touching anywhere else puts the row back
		const outside = (ev: PointerEvent) => {
			if (root?.contains(ev.target as Node)) return;
			document.removeEventListener('pointerdown', outside, true);
			if (closeOpen === close) close();
		};
		document.addEventListener('pointerdown', outside, true);
	}
	/** a tap or a scroll interrupted the row mid-flight: let it finish where it was heading */
	function settle() {
		if (x !== 0 && x !== -openW) (x < -openW / 2 ? open : close)();
	}
	function down(e: PointerEvent) {
		if (e.button !== 0 || committing) return;
		spring.stop();
		start = { x: e.clientX, y: e.clientY, id: e.pointerId, x0: x };
		locked = false;
		suppressClick = false;
		tracker.reset();
		tracker.add(e.clientX, 0);
	}
	function move(e: PointerEvent) {
		if (!start || e.pointerId !== start.id) return;
		const dx = e.clientX - start.x;
		const dy = e.clientY - start.y;
		if (!locked) {
			if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) {
				start = null;
				return settle();
			}
			// closed rows only open to the left; open rows can go either way
			const wants = Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy) * 1.2 && (start.x0 < 0 || dx < 0);
			if (!wants || !gesture.claim(token)) return;
			locked = true;
			suppressClick = true;
			if (closeOpen && closeOpen !== close) closeOpen();
			(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
			start.x = e.clientX; // no jump: follow from where the lock happened
		}
		tracker.add(e.clientX, 0);
		let nx = start.x0 + (e.clientX - start.x);
		if (nx > 0) nx = rubberband(nx, width || 300);
		spring.set(nx);
		const isArmed = -nx > full;
		if (isArmed !== armed) {
			armed = isArmed;
			tick10();
		}
	}
	function up() {
		if (!start) return settle();
		start = null;
		if (!locked) return settle();
		locked = false;
		gesture.release(token);
		const v = tracker.get().x;
		if (armed && v < 200) return commit(v);
		armed = false;
		const proj = x + project(v, 0.99);
		if (proj < -openW / 2) open(v);
		else close();
	}
	function commit(v = 0) {
		committing = true;
		armed = false;
		spring.to(-width, { response: 0.3, velocity: v }).then(() => {
			actions[0].run();
			committing = false;
			spring.set(0);
		});
	}
	function clickCapture(e: MouseEvent) {
		if (suppressClick) {
			e.stopPropagation();
			e.preventDefault();
			suppressClick = false;
		} else if (x < -1) {
			e.stopPropagation();
			e.preventDefault();
			close();
		}
	}
	function run(a: SwipeAction, i: number) {
		if (i === 0) return commit();
		close();
		a.run();
	}
</script>

<div class="swipe" bind:this={root} bind:clientWidth={width}>
	<div class="actions" class:rest={x === 0 && !committing} style:width="{Math.max(openW, -x)}px" aria-label={t('common.actions')}>
		{#each actions as a, i}
			<button
				type="button"
				class="act {a.tone}"
				class:grow={i === 0 && -x > openW}
				style:--w="{Math.max(ACTION_W, (-x / openW) * ACTION_W)}px"
				onfocus={() => x === 0 && open()}
				onclick={() => run(a, i)}>{a.label}</button
			>
		{/each}
	</div>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="swipe-front"
		style:transform={x ? `translate3d(${x}px,0,0)` : undefined}
		onpointerdown={down}
		onpointermove={move}
		onpointerup={up}
		onpointercancel={up}
		onclickcapture={clickCapture}
	>
		{@render children()}
	</div>
</div>

<style>
	.swipe {
		position: relative;
		overflow: hidden;
	}
	.swipe-front {
		position: relative;
		z-index: 1;
		touch-action: pan-y;
		/* rows stay glass: the actions are only shown while the row is moved aside */
		background: transparent;
	}
	.actions.rest {
		visibility: hidden;
	}
	.actions {
		position: absolute;
		top: 0;
		right: 0;
		bottom: 0;
		display: flex;
		flex-direction: row-reverse;
	}
	.act {
		flex: 1 0 var(--w);
		display: grid;
		place-items: center;
		padding: 0 8px;
		color: #fff;
		font-size: var(--f-sub);
		font-weight: 600;
	}
	.act.grow {
		flex-grow: 100;
	}
	.red {
		background: var(--red);
	}
	.orange {
		background: var(--orange);
	}
	.gray {
		background: #8e8e93;
	}
	.tint {
		background: var(--tint-fill);
		color: var(--on-tint);
	}
</style>
