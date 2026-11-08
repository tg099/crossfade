<script lang="ts" module>
	export interface Action {
		label: string;
		role?: 'destructive' | 'default';
		run: () => void;
	}
</script>

<script lang="ts">
	import { t } from './i18n.svelte.ts';
	import { tick, untrack } from 'svelte';
	import { Spring } from './motion.ts';

	// Confirmation for the few genuinely destructive moments. Phone: action sheet from the bottom.
	// Desktop: a compact alert. Escape or a tap outside cancels.
	let {
		open = $bindable(false),
		title,
		message = '',
		actions
	}: { open?: boolean; title: string; message?: string; actions: Action[] } = $props();

	let mounted = $state(false);
	let p = $state(0);
	let first: HTMLButtonElement | undefined = $state();
	let restore: HTMLElement | null = null;
	const spring = new Spring(0, (v) => (p = v), 0.002);

	$effect(() => {
		const o = open;
		untrack(async () => {
			if (o) {
				restore = document.activeElement as HTMLElement | null;
				mounted = true;
				await tick();
				first?.focus({ preventScroll: true });
				spring.to(1, { response: 0.32 });
			} else if (mounted) {
				const done = await spring.to(0, { response: 0.26 });
				if (done) {
					mounted = false;
					restore?.focus?.({ preventScroll: true });
				}
			}
		});
	});

	function choose(a?: Action) {
		open = false;
		a?.run();
	}
	function onkeydown(e: KeyboardEvent) {
		if (open && e.key === 'Escape') {
			e.stopPropagation();
			open = false;
		}
	}
</script>

<svelte:window {onkeydown} />

{#if mounted}
	<div class="layer" style:--p={p}>
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
		<div class="scrim" onclick={() => choose()}></div>
		<div class="box glass strong" role="alertdialog" aria-modal="true" aria-labelledby="as-title" aria-describedby="as-msg">
			<div class="text">
				<p id="as-title" class="title">{title}</p>
				{#if message}<p id="as-msg" class="msg">{message}</p>{/if}
			</div>
			<div class="acts">
				{#each actions as a, i}
					{#if i === 0}
						<button bind:this={first} class="act lq {a.role ?? ''}" onclick={() => choose(a)}>{a.label}</button>
					{:else}
						<button class="act lq {a.role ?? ''}" onclick={() => choose(a)}>{a.label}</button>
					{/if}
				{/each}
				<button class="act lq cancel" onclick={() => choose()}>{t('common.cancel')}</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.layer {
		position: fixed;
		inset: 0;
		z-index: 400;
	}
	.scrim {
		position: absolute;
		inset: 0;
		background: rgb(0 0 0 / calc(var(--p) * 0.25));
	}
	/* a floating glass card that grows out of the bottom of the screen */
	.box {
		position: absolute;
		left: 10px;
		right: 10px;
		bottom: max(10px, var(--safe-bottom));
		padding: 22px 16px 16px;
		border-radius: 34px;
		opacity: min(1, calc(var(--p) * 1.6));
		transform-origin: 50% 100%;
		transform: translate3d(0, calc((1 - var(--p)) * 40px), 0) scale(calc(0.9 + var(--p) * 0.1));
	}
	.text {
		padding: 0 8px 16px;
		text-align: center;
	}
	.title {
		font-size: var(--f-body);
		font-weight: 700;
	}
	.msg {
		margin-top: 4px;
		font-size: var(--f-sub);
		color: var(--label-2);
	}
	.acts {
		display: grid;
		gap: 8px;
	}
	.act {
		display: block;
		width: 100%;
		min-height: 52px;
		border-radius: 999px;
		background: var(--ctl);
		box-shadow: var(--ctl-shadow);
		font-size: var(--f-body);
		font-weight: 600;
	}
	.act.destructive {
		background: color-mix(in srgb, var(--red) 14%, transparent);
		color: var(--red-text);
	}
	@media (min-width: 721px) {
		.box {
			top: 50%;
			left: 50%;
			right: auto;
			bottom: auto;
			width: 320px;
			border-radius: 30px;
			transform-origin: 50% 50%;
			transform: translate(-50%, -50%) scale(calc(1.1 - var(--p) * 0.1));
		}
		.act {
			min-height: 44px;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.box {
			transform: none;
			opacity: var(--p);
		}
		@media (min-width: 721px) {
			.box {
				transform: translate(-50%, -50%);
			}
		}
	}
</style>
