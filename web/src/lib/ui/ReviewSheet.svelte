<script lang="ts">
	import { SERVICE_NAME } from '#lib/format.ts';
	import { player } from '#lib/player.svelte.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Issue } from '#lib/types.ts';
	import Cover from './Cover.svelte';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { project, Spring, tick10, VelocityTracker } from './motion.ts';
	import HearButton from './HearButton.svelte';
	import { seedColors } from './artcolor.ts';
	import { t } from './i18n.svelte.ts';
	import { fitting, ignoreIssue, loadIssues, resolveIssue, sameLength, unignore } from './issues.ts';
	import { tintVars } from './tint.ts';
	import { mmss, ui } from './ui.svelte.ts';

	// Unsure songs as a stack of cards: throw right to take the suggested version, left to ignore.
	// The card follows the finger in 2D, tilts around where you grabbed it, flies off with your
	// momentum. Ignoring can be undone; taking a version adds it to the service right away.
	const line = $derived(ui.reviewFade ? app.line(ui.reviewFade) : null);
	let queue = $state<Issue[]>([]);
	let idx = $state(0);
	let choice = $state<Record<number, number>>({});
	let links = $state<Record<number, string>>({});
	let log = $state<{ issue: Issue; how: 'take' | 'skip' }[]>([]);
	let loadedFor = -1;
	let loading = $state(false);

	// samples belong to the decision at hand: closing the stack ends them
	$effect(() => {
		if (!ui.reviewOpen) player.close();
	});
	$effect(() => {
		if (!ui.reviewOpen || !line || loadedFor === line.id) return;
		loadedFor = line.id;
		loading = true;
		idx = 0;
		log = [];
		choice = {};
		links = {};
		xs.set(0);
		ys.set(0);
		loadIssues(line.id)
			.then((all) => (queue = all.filter((i) => i.kind === 'unmatched')))
			.catch(ui.fail)
			.finally(() => (loading = false));
	});

	// ---------------------------------------------------------------- card physics
	let x = $state(0);
	let y = $state(0);
	let W = $state(360);
	let grabLow = $state(false);
	const xs = new Spring(0, (v) => (x = v), 0.5);
	const ys = new Spring(0, (v) => (y = v), 0.5);
	const tracker = new VelocityTracker();
	let drag: { x0: number; y0: number; id: number; started: boolean; cx: number; cy: number } | null = null;
	let armed: 'take' | 'skip' | null = null;
	let busy = false;

	const cur = $derived(queue[idx]);
	const tr = $derived(cur?.data.track);
	const cands = $derived(cur?.data.candidates ?? []);
	const next = $derived(queue[idx + 1]);
	const rot = $derived((x / Math.max(W, 1)) * 14 * (grabLow ? -1 : 1));
	const lift = $derived(Math.min(1, Math.abs(x) / (W * 0.5)));
	const takeO = $derived(Math.max(0, Math.min(1, x / (W * 0.28))));
	const skipO = $derived(Math.max(0, Math.min(1, -x / (W * 0.28))));

	function down(e: PointerEvent) {
		if (e.button !== 0 || busy || !cur) return;
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		W = r.width;
		grabLow = e.clientY > r.top + r.height / 2;
		xs.stop();
		ys.stop();
		drag = { x0: e.clientX, y0: e.clientY, id: e.pointerId, started: false, cx: x, cy: y };
		tracker.reset();
		tracker.add(e.clientX, e.clientY);
	}
	function move(e: PointerEvent) {
		if (!drag || drag.id !== e.pointerId) return;
		const dx = e.clientX - drag.x0;
		const dy = e.clientY - drag.y0;
		if (!drag.started) {
			if (Math.hypot(dx, dy) < 8) return;
			drag.started = true;
			(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		}
		tracker.add(e.clientX, e.clientY);
		xs.set(drag.cx + dx);
		ys.set(drag.cy + dy * 0.5);
		const now = x > W * 0.42 ? 'take' : x < -W * 0.42 ? 'skip' : null;
		if (now !== armed) {
			armed = now;
			if (now) tick10();
		}
	}
	function up() {
		if (!drag) return;
		const started = drag.started;
		drag = null;
		if (!started) return settle();
		const v = tracker.get();
		const px = x + project(v.x, 0.995);
		const dir = Math.abs(v.x) > 700 ? Math.sign(v.x) : Math.abs(px) > W * 0.45 ? Math.sign(px) : 0;
		armed = null;
		if (dir > 0) fly('take', v.x, v.y);
		else if (dir < 0) fly('skip', v.x, v.y);
		else {
			// let go without intent: spring home with a little give, the throw carried momentum
			xs.to(0, { response: 0.42, damping: 0.72, velocity: v.x });
			ys.to(0, { response: 0.42, damping: 0.72, velocity: v.y });
		}
	}
	function settle() {
		if (x || y) {
			xs.to(0, { response: 0.4 });
			ys.to(0, { response: 0.4 });
		}
	}

	/** a suggestion is only chosen for you when it convincingly is the song; otherwise you pick */
	const preset = (i: Issue) => (i.data.candidates ?? []).findIndex(fitting);
	const target = (i: Issue) => links[i.id]?.trim() || i.data.candidates?.[pick(i)]?.id || '';

	async function fly(how: 'take' | 'skip', vx = 0, vy = 0) {
		if (!cur || busy || !line) return;
		const issue = cur;
		if (how === 'take' && !target(issue)) {
			// nothing to take yet: spring back and point at the link field
			xs.to(0, { response: 0.4, damping: 0.7 });
			ys.to(0, { response: 0.4, damping: 0.7 });
			ui.say(t('review.needLink'), 'link', 'info');
			return;
		}
		busy = true;
		const dir = how === 'take' ? 1 : -1;
		const vxOut = dir * Math.max(Math.abs(vx), 1400);
		ys.to(y + vy * 0.25, { response: 0.5, velocity: vy });
		const flying = xs.to(dir * W * 1.5, { response: 0.36, velocity: vxOut });
		const ok = how === 'take' ? await resolveIssue(line.id, issue, target(issue)) : await ignoreIssue(line.id, issue, true);
		await flying;
		if (ok) {
			log.push({ issue, how });
			idx += 1;
			xs.set(0);
		} else xs.to(0, { response: 0.42, damping: 0.8 });
		ys.set(0);
		busy = false;
	}

	/** only ignoring can be taken back */
	const canUndo = $derived(log.length > 0 && log[log.length - 1].how === 'skip');

	async function undo() {
		if (!canUndo || !line || busy) return;
		busy = true;
		const last = log.pop()!;
		if (await unignore(line.id, last.issue)) {
			idx -= 1;
			// it comes back in from the side it left to
			xs.set(-W * 1.3);
			ys.set(0);
			await xs.to(0, { response: 0.42, damping: 0.82 });
		} else log.push(last);
		busy = false;
	}

	function key(e: KeyboardEvent) {
		if (e.target instanceof HTMLInputElement) return;
		if (e.key === 'ArrowRight') fly('take');
		else if (e.key === 'ArrowLeft') fly('skip');
		else if ((e.metaKey || e.ctrlKey) && e.key === 'z') undo();
		else return;
		e.preventDefault();
	}
	const taken = $derived(log.filter((l) => l.how === 'take').length);
	const skipped = $derived(log.length - taken);
	function pick(i: Issue) {
		return choice[i.id] ?? preset(i);
	}
	const cardColors = $derived(cur ? seedColors(cur.data.track.id) : []);
</script>

<Sheet
	bind:open={ui.reviewOpen}
	label={t('review.title')}
	class="tinted"
	style={line ? tintVars(line.color) : ''}
	onclosed={() => {
		loadedFor = -1;
		player.close();
		if (log.length) ui.say(t('review.summary', { taken, skipped }));
	}}
>
	{#snippet header()}
		<SheetBar
			title={cur ? t('review.count', { i: idx + 1, n: queue.length }) : t('review.doneShort')}
			leading={{ label: t('common.close'), run: () => (ui.reviewOpen = false) }}
		/>
	{/snippet}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="review" onkeydown={key} tabindex="-1">
		{#if loading}
			<p class="t-sub t-2 wait">{t('review.loading')}</p>
		{:else if cur && line}
			<div class="deck" bind:clientWidth={W}>
				{#if next}
					<div class="card under" style:transform="translateY({(1 - lift) * 14}px) scale({0.94 + lift * 0.06})" aria-hidden="true"></div>
				{/if}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div
					class="card top"
					style:--c0={cardColors[0]}
					style:--c1={cardColors[1]}
					style:transform="translate3d({x}px, {y}px, 0) rotate({rot}deg)"
					style:transform-origin={grabLow ? '50% 0%' : '50% 100%'}
					onpointerdown={down}
					onpointermove={move}
					onpointerup={up}
					onpointercancel={up}
				>
					<span class="stamp take" style:opacity={takeO}>{t('review.take')}</span>
					<span class="stamp skip" style:opacity={skipO}>{t('review.skip')}</span>
					<!-- the song itself, compact: the decision is made in the versions below -->
					<div class="rhead">
						<div class="rcover">
							<Cover src={tr.artwork} seed={tr.id} size={96} radius={0.14} track={tr} />
							<span class="play"><HearButton track={tr} size={36} onArt /></span>
						</div>
						<div class="rmeta">
							<p class="t-title3 rtitle">{tr.title}</p>
							<p class="t-sub t-2 clip">{tr.artist}{tr.duration ? ` · ${mmss(tr.duration)}` : ''}</p>
						</div>
					</div>
					<p class="why t-foot">{cur.data.reason ? `${cur.data.reason} · ` : ''}{t('review.for', { service: `${SERVICE_NAME[cur.service]} · ${cur.collection_name}` })}</p>
					{#if cands.length}
						{#if pick(cur) < 0 && !links[cur.id]}<p class="t-foot t-2 unsure">{t('review.noneSure')}</p>{/if}
						<div class="cands" role="radiogroup" aria-label={t('review.versionIn', { service: SERVICE_NAME[cur.service] })}>
							{#each cands as c, ci (c.id)}
								<div class="cand" class:on={pick(cur) === ci && !links[cur.id]}>
									<HearButton track={c} />
									<button class="cand-pick" role="radio" aria-checked={pick(cur) === ci && !links[cur.id]} onclick={() => ((choice[cur.id] = ci), (links[cur.id] = ''))}>
										<span class="grow">
											<span class="b clip">{c.title}</span>
											<span class="b t-foot t-2 clip">{#if c.version && c.version !== 'Original'}<span class="tag warn ver">{c.version}</span> {/if}{c.album ? `${c.album} · ` : ''}{mmss(c.duration)}{c.exact ? ` · ${t('issue.exact')}` : sameLength(c, tr) ? ` · ${t('review.sameLength')}` : ''}</span>
										</span>
										<span class="radio" class:on={pick(cur) === ci && !links[cur.id]}>{#if pick(cur) === ci && !links[cur.id]}<Glyph name="check" size={13} weight={3.2} />{/if}</span>
									</button>
								</div>
							{/each}
						</div>
					{:else}
						<p class="t-foot t-2 nocand">{t('issue.noCandidates')}</p>
					{/if}
					<label class="field linkfield">
						<Glyph name="link" size={16} weight={2.2} />
						<input bind:value={links[cur.id]} placeholder={t('issue.linkPh', { service: SERVICE_NAME[cur.service] })} autocomplete="off" spellcheck="false" />
					</label>
				</div>
			</div>

			<div class="foot">
			<div class="actions">
				<button class="act glass lq" onclick={() => fly('skip')} aria-label={t('review.skip')}><Glyph name="x" size={26} weight={2.6} /></button>
				<button class="act sm glass lq" onclick={undo} disabled={!canUndo} aria-label={t('common.undo')}><Glyph name="undo" size={20} weight={2.4} /></button>
				<button class="act glass tint lq" class:idle={!target(cur)} onclick={() => fly('take')} aria-label={t('review.take')}><Glyph name="check" size={28} weight={2.8} /></button>
			</div>
			<p class="rhint t-foot t-2">{t('review.hint')}</p>
			</div>
		{:else}
			<div class="done">
				<span class="done-ic"><Glyph name="check" size={36} weight={2.6} /></span>
				<p class="t-title2">{t('review.done')}</p>
				<p class="t-sub t-2">{t('review.doneText', { taken, skipped })}</p>
				<div class="done-row">
					{#if canUndo}<button class="pb glass lq" onclick={undo}><Glyph name="undo" size={18} weight={2.4} /> {t('common.undo')}</button>{/if}
					<button class="pb glass tint lq" onclick={() => (ui.reviewOpen = false)}>{t('common.done')}</button>
				</div>
			</div>
		{/if}
	</div>
</Sheet>

<style>
	.review {
		display: grid;
		/* never wider than the sheet, however long a title is */
		grid-template-columns: minmax(0, 1fr);
		justify-items: center;
		overflow-x: clip; /* a card flying out must not widen the sheet */
		padding: 6px 18px calc(var(--safe-bottom) + 24px);
		outline: none;
	}
	.deck {
		position: relative;
		width: min(100%, 380px);
		min-width: 0;
	}
	.card {
		border-radius: 30px;
		background: var(--sheet-cell);
		box-shadow: 0 18px 40px -18px rgb(0 0 0 / 0.35);
	}
	.under {
		position: absolute;
		inset: 0;
		padding: 14px;
		overflow: hidden;
		opacity: 0.9;
	}
	.top {
		position: relative;
		min-width: 0;
		padding: 14px 14px 12px;
		/* opaque: the next card waits underneath and must not show through */
		background:
			radial-gradient(120% 70% at 50% 0%, color-mix(in srgb, var(--c0) 22%, transparent), transparent 70%),
			linear-gradient(var(--sheet-cell), var(--sheet-cell)),
			var(--bg);
		touch-action: none;
		user-select: none;
		-webkit-user-select: none;
		cursor: grab;
		will-change: transform;
	}
	.top:active {
		cursor: grabbing;
	}
	.rhead {
		display: flex;
		align-items: center;
		gap: 14px;
		min-width: 0;
	}
	.rcover {
		position: relative;
		flex: none;
	}
	.play {
		position: absolute;
		right: -6px;
		bottom: -6px;
	}
	.rmeta {
		min-width: 0;
	}
	/* long titles wrap to two lines instead of being cut */
	.rtitle {
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.why {
		display: inline-block;
		margin: 8px 0 10px;
		padding: 4px 10px;
		border-radius: 999px;
		background: color-mix(in srgb, var(--orange) 16%, transparent);
		color: var(--orange-text);
		font-weight: 600;
	}
	.cands {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 6px;
	}
	.cand {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
		padding: 7px 12px 7px 8px;
		border-radius: 18px;
		background: var(--fill-3);
	}
	.cand.on {
		background: color-mix(in srgb, var(--tint-fill) 14%, transparent);
	}
	.cand-pick {
		flex: 1;
		min-width: 0;
		display: flex;
		align-items: center;
		gap: 10px;
		text-align: left;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.b {
		display: block;
	}
	.radio {
		display: grid;
		place-items: center;
		flex: none;
		width: 22px;
		height: 22px;
		border-radius: 50%;
		box-shadow: inset 0 0 0 1.5px var(--label-3);
		color: var(--on-tint);
	}
	.radio.on {
		background: var(--tint-fill);
		box-shadow: none;
	}
	.stamp {
		position: absolute;
		top: 26px;
		z-index: 2;
		padding: 6px 14px;
		border-radius: 12px;
		border: 3px solid currentColor;
		font-size: var(--f-t3);
		font-weight: 900;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		background: rgb(255 255 255 / 0.85);
		pointer-events: none;
	}
	.stamp.take {
		left: 26px;
		color: var(--green-text);
		rotate: -12deg;
	}
	.stamp.skip {
		right: 26px;
		color: var(--label-2);
		rotate: 12deg;
	}
	/* stays in reach while a long list of versions scrolls underneath */
	.actions {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 22px;
	}
	.act {
		display: grid;
		place-items: center;
		width: 68px;
		height: 68px;
		border-radius: 50%;
		color: var(--label);
	}
	.act.sm {
		width: 50px;
		height: 50px;
		color: var(--label-2);
	}
	.act:disabled {
		color: var(--label-3);
	}
	/* nothing chosen yet: the check waits for a pick (tapping it says so) */
	.act.idle {
		opacity: 0.45;
	}
	.unsure {
		margin: 0 4px 8px;
	}
	.ver {
		margin-right: 2px;
		vertical-align: 1px;
	}
	/* phones: the actions take less height, so more versions stay in view above them */
	@media (max-width: 720px) {
		.foot {
			padding-top: 16px;
		}
		.act {
			width: 60px;
			height: 60px;
		}
		.act.sm {
			width: 46px;
			height: 46px;
		}
	}
	@media (max-width: 720px) and (max-height: 800px) {
		.rhint {
			display: none;
		}
	}
	/* actions stay in reach and sit on their own ground: nothing scrolls visibly behind them */
	.foot {
		position: sticky;
		bottom: 0;
		z-index: 2;
		display: grid;
		justify-items: center;
		width: calc(100% + 36px);
		margin: 0 -18px calc(-1 * (var(--safe-bottom) + 24px));
		padding: 28px 18px calc(var(--safe-bottom) + 16px);
		background: linear-gradient(to bottom, transparent, var(--sheet-solid, var(--bg)) 26px);
	}
	.wait {
		padding: 40px 20px;
		text-align: center;
	}
	.nocand {
		padding: 4px 4px 2px;
	}
	.linkfield {
		margin-top: 8px;
		font-size: var(--f-sub);
	}
	.rhint {
		margin-top: 10px;
		text-align: center;
		max-width: 300px;
	}
	.done {
		display: grid;
		justify-items: center;
		gap: 8px;
		padding: 60px 20px;
		text-align: center;
	}
	.done-ic {
		display: grid;
		place-items: center;
		width: 76px;
		height: 76px;
		margin-bottom: 8px;
		border-radius: 50%;
		background: color-mix(in srgb, var(--green) 18%, transparent);
		color: var(--green-text);
		animation: pop 600ms cubic-bezier(0.25, 1.6, 0.45, 1);
	}
	@keyframes pop {
		from {
			transform: scale(0.4);
			opacity: 0;
		}
	}
	.done-row {
		display: flex;
		gap: 10px;
		margin-top: 14px;
	}
	@media (prefers-reduced-motion: reduce) {
		.done-ic {
			animation: none;
		}
	}
</style>
