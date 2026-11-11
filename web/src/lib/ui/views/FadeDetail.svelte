<script lang="ts">
	import { untrack } from 'svelte';
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { SERVICE_NAME } from '#lib/format.ts';
	import { api, del, post, put } from '#lib/api.ts';
	import { app } from '#lib/state.svelte.ts';
	import type { Change, FadeStats as Stats, Issue, Job, Line, LiveItem, Member, Plan, Role, VersionAuditEntry } from '#lib/types.ts';
	import Cover from '../Cover.svelte';
	import FadeBadge from '../FadeBadge.svelte';
	import FadeStats from '../FadeStats.svelte';
	import Glyph from '../Glyph.svelte';
	import IssueSheet from '../IssueSheet.svelte';
	import Menu from '../Menu.svelte';
	import Mosaic from '../Mosaic.svelte';
	import Page from '../Page.svelte';
	import PlanList from '../PlanList.svelte';
	import Segmented from '../Segmented.svelte';
	import SwipeRow from '../SwipeRow.svelte';
	import { askLeaveFade, hold, runFade, stopFade, trackMenu } from '../ctx.ts';
	import { changeNote, fadeCovers, fadeIcon, fadeStatus, memberFault, ruleText, toneColor } from '../fade.ts';
	import { i18n, num, t } from '../i18n.svelte.ts';
	import { ignoreIssue, loadIssues, resolveIssue, restoreIssue, unignore } from '../issues.ts';
	import { tintVars } from '../tint.ts';
	import { BRAND, flowIcon, intervalLabel, ui } from '../ui.svelte.ts';

	let { id, back }: { id: number; back?: { label: string; run: () => void } } = $props();

	const fade = $derived(app.line(id));
	const status = $derived(fade ? fadeStatus(fade) : null);
	const memberById = $derived(new Map((fade?.members ?? []).map((m) => [m.id, m])));

	type Tab = 'changes' | 'history' | 'songs';
	let tab = $state<Tab>('changes');
	let plan = $state<Plan | null>(null);
	let issues = $state<Issue[]>([]);
	let changes = $state<Change[]>([]);
	let songs = $state<{ total: number; items: any[] }>({ total: 0, items: [] });
	let songQuery = $state('');
	let songMode = $state<'all' | 'versions'>('all');
	let audit = $state<VersionAuditEntry[] | null>(null);
	let stats = $state<Stats | null>(null);

	// ---------------------------------------------------------------- results of a running job
	type Live = { started_at: number; n: number; plan: Plan; issues: Issue[]; changes: Change[] };
	let live = $state<Live | null>(null);
	let holdLive = $state(false); // keep showing them until the stored results are loaded
	let livePulling = false;
	const liveView = $derived(fade?.job || holdLive ? live : null);

	function emptyLive(job: Job): Live {
		return {
			started_at: job.started_at,
			n: 0,
			plan: { run_id: 0, trigger: job.mode === 'preview' ? 'preview' : job.trigger, started_at: job.started_at, status: 'running', summary: {}, plan: { summary: {}, members: [], held: [], unmatched: [] } },
			issues: [],
			changes: []
		};
	}

	function applyLive(l: Live, items: LiveItem[]) {
		for (const it of items) {
			const n = ++l.n;
			if (it.kind === 'start') l.plan.plan.members = it.members.map((m) => ({ ...m, add: [], remove: [], pending: [] }));
			else if (it.kind === 'plan') {
				if (it.group === 'held') l.plan.plan.held.push(it.track);
				else l.plan.plan.members.find((m) => m.member_id === it.member_id)?.[it.group].push(it.track);
			} else if (it.kind === 'issue') {
				const m = memberById.get(it.member_id);
				l.issues.push({
					id: -n,
					kind: 'unmatched',
					member_id: it.member_id,
					song_id: it.song_id,
					collection_name: m?.collection_name ?? '',
					service: m?.service ?? (it.track.service || 'ytm'),
					created_at: Date.now() / 1000,
					data: { track: it.track, candidates: it.candidates, reason: it.reason }
				} as Issue);
			} else if (it.kind === 'change') {
				const { kind: _k, song_id: _s, ...c } = it;
				l.changes.unshift({ id: -n, ...c } as Change);
			}
		}
	}

	async function pullLive() {
		if (livePulling) return;
		livePulling = true;
		try {
			for (;;) {
				const job = fade?.job;
				if (!job) break;
				if (!live || live.started_at !== job.started_at) live = emptyLive(job);
				if ((job.live_n ?? 0) <= live.n) break;
				const r = await api<{ running: boolean; started_at: number | null; items: LiveItem[] }>(`/api/lines/${id}/live?since=${live.n}`);
				if (!r.running || r.started_at !== live.started_at) break;
				applyLive(live, r.items);
			}
		} catch {
			// not essential: the stored results arrive when the run is done
		} finally {
			livePulling = false;
		}
	}
	$effect(() => {
		const job = fade?.job;
		job?.live_n;
		if (job) untrack(pullLive);
	});

	const shownPlan = $derived(liveView?.plan ?? plan);
	const shownIssues = $derived(liveView ? [...liveView.issues, ...issues.filter((i) => i.kind === 'ignored')] : issues);
	const shownChanges = $derived(liveView ? [...liveView.changes, ...changes] : changes);
	const openIssues = $derived(shownIssues.filter((i) => i.kind === 'unmatched'));
	const ignored = $derived(shownIssues.filter((i) => i.kind === 'ignored'));
	let showIgnored = $state(false);
	// a long list of open songs would push everything else away: show a few, "Durchgehen" does the rest
	const ISSUE_PEEK = 5;
	let allIssues = $state(false);

	// ---------------------------------------------------------------- loading
	async function loadAll() {
		const [p, i, c] = await Promise.all([
			api<Plan | null>(`/api/lines/${id}/plan`),
			loadIssues(id),
			api<Change[]>(`/api/lines/${id}/changes?limit=200`)
		]);
		plan = p;
		issues = i;
		changes = c;
		// not essential: the page works without it
		api<Stats>(`/api/lines/${id}/stats`).then((s) => (stats = s), () => {});
		if (tab === 'songs') loadSongs();
	}
	async function loadSongs() {
		songs = await api(`/api/lines/${id}/songs?limit=300&q=${encodeURIComponent(songQuery)}`);
		if (songMode === 'versions') loadAudit();
	}
	async function loadAudit() {
		audit = await api<VersionAuditEntry[]>(`/api/lines/${id}/versions`);
	}
	$effect(() => {
		id;
		untrack(() => {
			stats = null; // another fade: never show the previous one's numbers
			loadAll().catch(() => {});
		});
	});
	// a run of this fade finished: reload, keep the live list until then
	$effect(() => {
		const f = app.finished;
		if (f && f.line_id === id)
			untrack(() => {
				holdLive = !!live;
				loadAll().finally(() => {
					live = null;
					holdLive = false;
				});
			});
	});
	// a version was swapped in the song sheet
	$effect(() => {
		if (ui.songChanged) untrack(() => tab === 'songs' && loadSongs());
	});
	let songTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		songQuery;
		if (tab !== 'songs') return;
		clearTimeout(songTimer);
		songTimer = setTimeout(loadSongs, 250);
	});
	$effect(() => {
		if (tab === 'songs' && songMode === 'versions' && audit === null) untrack(loadAudit);
	});

	// ---------------------------------------------------------------- unsure songs
	let pickOpen = $state(false);
	let picking = $state<Issue | null>(null);
	const reload = () => loadIssues(id).then((i) => (issues = i));

	async function quickTake(i: Issue) {
		const c = i.data.candidates?.[0];
		if (!c) return pick(i);
		if (await resolveIssue(id, i, c.id)) reload();
	}
	async function quickIgnore(i: Issue) {
		if (await ignoreIssue(id, i, true)) {
			reload();
			ui.say(t('issue.ignored', { title: i.data.track.title }), 'x', 'info', async () => {
				await unignore(id, i);
				reload();
			});
		}
	}
	function pick(i: Issue) {
		picking = i;
		pickOpen = true;
	}

	function confirmHeld() {
		const n = shownPlan?.plan.held.length || fade?.last_summary?.held || 0;
		ui.ask = { title: t('plan.confirmQ', { n }), message: t('plan.confirmMsg'), label: t('plan.confirm'), run: () => runFade(id, false, true) };
	}

	// ---------------------------------------------------------------- history by day
	const days = $derived.by(() => {
		const out: { day: string; items: Change[] }[] = [];
		const today = new Date().setHours(0, 0, 0, 0);
		for (const c of shownChanges) {
			const d = new Date(c.ts * 1000);
			const diff = Math.round((today - new Date(c.ts * 1000).setHours(0, 0, 0, 0)) / 86400000);
			const day = diff === 0 ? t('day.today') : diff === 1 ? t('day.yesterday') : d.toLocaleDateString(i18n.locale, { weekday: 'long', day: 'numeric', month: 'long' });
			const last = out[out.length - 1];
			if (last && last.day === day) last.items.push(c);
			else out.push({ day, items: [c] });
		}
		return out;
	});
	const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
	const follows = (m: { role: string; follow_deletions: boolean }) => m.role === 'send' && m.follow_deletions && !!fade?.sync_deletions;
	const roleIcon = (r: string) => (r === 'both' ? 'both' : r === 'send' ? 'right' : 'left');
	const fault = $derived(fade?.members.find((m) => memberFault(m)));
	const isNew = $derived(fade && !fade.last_run_at && !fade.job);
	const interchange = $derived(fade?.warnings.filter((w) => w.kind === 'interchange') ?? []);
	const STATE_KEY = { ok: 'songs.ok', pending: 'songs.pending', excluded: 'songs.excluded', missing: 'songs.missing' } as const;

	// ---------------------------------------------------------------- sharing with friends
	const owner = $derived(fade?.owner.display_name ?? '');
	/** friends with a collection in this fade (owner's view) */
	const guests = $derived.by(() => {
		const seen = new Map<number, { id: number; name: string; n: number }>();
		for (const m of fade?.members ?? []) {
			if (m.user_id === fade?.owner.id) continue;
			const g = seen.get(m.user_id) ?? { id: m.user_id, name: m.user_name, n: 0 };
			g.n++;
			seen.set(m.user_id, g);
		}
		return [...seen.values()];
	});
	const invitable = $derived(
		app.friends.friends.filter((p) => !guests.some((g) => g.id === p.id) && !fade?.invites.some((i) => i.user.id === p.id))
	);
	let inviteBtn: HTMLButtonElement | undefined = $state();
	let inviteOpen = $state(false);

	function applyLine(fresh: Line) {
		const i = app.lines.findIndex((l) => l.id === fresh.id);
		if (i >= 0) app.lines[i] = fresh;
	}
	async function invite(p: { id: number; display_name: string }) {
		try {
			applyLine(await post<Line>(`/api/lines/${id}/invites`, { user_id: p.id }));
			ui.say(t('share.invited', { name: p.display_name }), 'users');
		} catch (e) {
			ui.fail(e);
		}
	}
	async function cancelInvite(inv: { id: number; user: { display_name: string } }) {
		try {
			applyLine(await del<Line>(`/api/lines/${id}/invites/${inv.id}`));
			ui.say(t('share.cancelled', { name: inv.user.display_name }), 'x', 'info');
		} catch (e) {
			ui.fail(e);
		}
	}
	// a friend asked to join with one of their collections: the owner lets it in (maybe with
	// another direction) or says no, which keeps the invite open for another pick
	type Invite = Line['invites'][number];
	const requests = $derived(fade?.invites.filter((i) => i.request) ?? []);
	const waiting = $derived(fade?.invites.filter((i) => !i.request) ?? []);
	let reqRole = $state<Record<number, Role>>({});
	let reqFor = $state<Invite | null>(null);
	let reqBtn: HTMLButtonElement | undefined = $state();
	let reqOpen = $state(false);
	const askedRole = (inv: Invite) => reqRole[inv.id] ?? inv.request!.role;
	async function approve(inv: Invite) {
		try {
			const role = askedRole(inv);
			applyLine(await post<Line>(`/api/lines/${id}/invites/${inv.id}/approve`, { role, follow_deletions: role === 'send' && inv.request!.follow_deletions }));
			app.loadFriends();
			ui.say(t('share.approved', { name: inv.user.display_name }), 'users');
		} catch (e) {
			ui.fail(e);
		}
	}
	async function reject(inv: Invite) {
		try {
			applyLine(await post<Line>(`/api/lines/${id}/invites/${inv.id}/reject`));
			ui.say(t('share.rejected', { name: inv.user.display_name }), 'x', 'info');
		} catch (e) {
			ui.fail(e);
		}
	}
	function removeGuest(g: { id: number; name: string }) {
		ui.ask = {
			title: t('share.removeQ', { name: g.name }),
			message: t('share.removeMsg', { name: g.name }),
			label: t('share.remove'),
			run: async () => {
				try {
					applyLine(await del<Line>(`/api/lines/${id}/guests/${g.id}`));
					app.loadFriends();
					ui.say(t('share.removed', { name: g.name }), 'trash');
				} catch (e) {
					ui.fail(e);
				}
			}
		};
	}
	// the direction of a collection, right here: a friend for their own one, the owner for every one
	let roleFor = $state<Member | null>(null);
	let roleBtn: HTMLButtonElement | undefined = $state();
	let roleOpen = $state(false);
	function chooseRole(m: Member, el: HTMLButtonElement) {
		roleFor = m;
		roleBtn = el;
		roleOpen = true;
	}
	async function setRole(m: Member, role: Role) {
		if (role === m.role) return;
		try {
			applyLine(await put<Line>(`/api/lines/${id}/members/${m.id}`, { role, follow_deletions: m.follow_deletions }));
		} catch (e) {
			ui.fail(e);
		}
	}
</script>

{#if fade && status}
	<div class="tinted wrap" style={tintVars(fade.color)}>
		<Page title={fade.name} large={false} {back} glow={BRAND}>
			{#snippet actions()}
				{#if fade.mine}
					<button class="gbtn glass lq" onclick={() => ui.edit(fade.id)}>{t('common.edit')}</button>
				{:else}
					<button class="gbtn glass lq" onclick={() => askLeaveFade(fade.id)}>{t('share.leave')}</button>
				{/if}
			{/snippet}

			{#snippet hero()}
				<header class="hero">
					<Mosaic covers={fadeCovers(fade)} seed={fade.name} color={fade.color} size="var(--cover)" />
					<div class="meta">
						<p class="eyebrow t-foot"><FadeBadge icon={fadeIcon(fade)} color={fade.color} size={20} /> {fade.mine ? t('fade.one') : t('share.from', { name: owner })}</p>
						<h1 class="t-title1" data-title>{fade.name}</h1>
						<p class="sub t-sub">{t('n.collections', { n: fade.members.length })} · {t('n.songs', { n: fade.songs })}</p>
						<p class="status t-foot">
							<span class="dot" class:pulse={status.tone === 'running'} style:background={status.tone === 'running' ? 'var(--tint-fill)' : toneColor[status.tone]} aria-hidden="true"></span>{status.text}
						</p>
						<div class="cta">
							{#if fade.job}
								<button class="pb glass lq" onclick={() => stopFade(fade.id)}><Glyph name="stop" size={16} /> {t('fade.stop')}</button>
							{:else}
								<button class="pb glass tint lq" onclick={() => runFade(fade.id)} disabled={!!fault || fade.members.length < 2}><Glyph name="refresh" size={18} weight={2.4} /> {t('fade.sync')}</button>
								<button class="pb glass lq" onclick={() => runFade(fade.id, true)} disabled={!!fault || fade.members.length < 2}><Glyph name="eye" size={18} weight={2.2} /> {t('fade.preview')}</button>
							{/if}
						</div>
					</div>
				</header>
			{/snippet}

			<div class="content">
				{#if fade.job}
					<section class="card progress" aria-live="polite">
						<div class="p-row">
							<span class="t-sub"><b>{fade.job.state === 'queued' ? t('status.queued') : fade.job.phase || status.text}</b></span>
							{#if fade.job.total}<span class="t-foot t-2">{t('job.of', { done: num(fade.job.done), total: num(fade.job.total) })}</span>{/if}
						</div>
						<span class="bar"><span class="fill" class:indet={!fade.job.total} style:width="{fade.job.total ? (fade.job.done / fade.job.total) * 100 : 30}%"></span></span>
					</section>
				{/if}

				{#if fault && !fault.mine}
					<section class="card fault">
						<span class="tile-icon" style:background="var(--red)"><Glyph name="key" size={18} weight={2.2} /></span>
						<div class="grow">
							<p><b>{t('share.friendFault', { name: fault.user_name, service: SERVICE_NAME[fault.service] })}</b></p>
							<p class="t-foot t-2">{t('share.friendFaultHint', { name: fault.user_name })}</p>
						</div>
					</section>
				{:else if fault}
					<section class="card fault">
						<span class="tile-icon" style:background="var(--red)"><Glyph name="key" size={18} weight={2.2} /></span>
						<div class="grow">
							<p><b>{memberFault(fault)}</b></p>
							<p class="t-foot t-2">{t('fade.faultHint')}</p>
						</div>
						<button class="pb pb-fill pb-small press" onclick={() => ui.openAccount(fault.service)}>{t('services.signin')}</button>
					</section>
				{:else if fade.last_status === 'needs_confirm' && !fade.job && fade.mine}
					<section class="card fault">
						<span class="tile-icon" style:background="var(--orange)"><Glyph name="trash" size={18} weight={2.2} /></span>
						<div class="grow">
							<p><b>{t('status.held', { n: fade.last_summary?.held ?? 0 })}</b></p>
							<p class="t-foot t-2">{t('status.heldDetail')}</p>
						</div>
						<div class="col">
							<button class="pb pb-fill pb-small press" onclick={confirmHeld}>{t('plan.confirmShort')}</button>
							<button class="text-btn t-sub" onclick={() => ui.showPlan(fade.id)}>{t('plan.showList')}</button>
						</div>
					</section>
				{:else if isNew}
					<section class="card fault">
						<span class="tile-icon" style:background="var(--tint-fill)"><Glyph name="sparkle" size={18} weight={2.2} /></span>
						<div class="grow">
							<p><b>{t('fade.firstTitle')}</b></p>
							<p class="t-foot t-2">{t('fade.firstText')}</p>
						</div>
						<button class="pb pb-fill pb-small press" onclick={() => runFade(fade.id, true)}>{t('fade.preview')}</button>
					</section>
				{/if}

				{#each interchange as w}
					<p class="notice t-foot">{w.text}</p>
				{/each}

				{#if openIssues.length}
					<section class="section" aria-labelledby="iss-h">
						<div class="section-head">
							<h2 id="iss-h">{t('issue.head', { n: openIssues.length })}</h2>
							{#if !liveView}<button class="gbtn glass tint lq go-review" onclick={() => ui.review(fade.id)}>{t('issue.goThrough')}</button>{/if}
						</div>
						<ul class="group">
							{#each allIssues || liveView ? openIssues : openIssues.slice(0, ISSUE_PEEK) as i (i.id)}
								{@const tr = i.data.track}
								<li>
									<SwipeRow
										actions={[
											{ label: t('review.skip'), tone: 'gray', run: () => quickIgnore(i) },
											...(i.data.candidates?.length ? [{ label: t('review.take'), tone: 'tint' as const, run: () => quickTake(i) }] : [])
										]}
									>
										<button class="cell" style:--inset="74px" onclick={() => !liveView && pick(i)} disabled={i.id < 0} use:hold={() => trackMenu(tr)}>
											<Cover src={tr.artwork} seed={tr.id} size={44} track={tr} />
											<span class="grow">
												<span class="clip block">{tr.title}</span>
												<span class="t-foot t-2 clip block">{tr.artist} · {t('issue.missingIn', { name: i.collection_name, service: SERVICE_NAME[i.service] })}</span>
											</span>
											<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
										</button>
									</SwipeRow>
								</li>
							{/each}
							{#if !liveView && openIssues.length > ISSUE_PEEK}
								<li>
									<button class="cell" style:--inset="74px" onclick={() => (allIssues = !allIssues)}>
										<span class="grow tintc">{allIssues ? t('common.less') : t('lib.moreN', { n: num(openIssues.length - ISSUE_PEEK) })}</span>
										<span class="chev" class:up={allIssues}><Glyph name="chevronDown" size={18} weight={2.4} /></span>
									</button>
								</li>
							{/if}
						</ul>
						<p class="section-foot">{liveView ? t('issue.liveHint') : t('issue.hint')}</p>
					</section>
				{/if}

				<section class="section" aria-labelledby="stops-h">
					<div class="section-head"><h2 id="stops-h">{t('editor.collections')}</h2></div>
					<ol class="group route">
						{#each fade.members as m, i (m.id)}
							{@const others = (app.interchanges.get(`${m.account_id}:${m.kind}:${m.collection_id}`) ?? []).filter((o) => o.id !== fade.id)}
							{@const sorted = m.role !== 'send' && !!m.rule}
							{@const handsOff = m.role !== 'receive' && m.move_sorted}
							<li class="stop" class:first={i === 0} class:last={i === fade.members.length - 1}>
								<span class="rail" aria-hidden="true">
									<span class="rseg up"></span>
									<span class="node"></span>
									<span class="rseg down"></span>
									{#if i < fade.members.length - 1}
										<span class="flow {flowIcon(m, fade.members[i + 1])}"><Glyph name={flowIcon(m, fade.members[i + 1])} size={12} weight={2.8} /></span>
									{/if}
								</span>
								<div class="cell" style:--inset="88px">
									<ServiceMark service={m.service} size={30} />
									<span class="grow">
										<span class="clip block">{m.collection_name}</span>
										<span class="t-foot t-2 clip block">
											{memberFault(m) ?? `${SERVICE_NAME[m.service]} · ${m.initialized && m.item_count != null ? t(m.kind === 'liked' ? 'n.likes' : 'n.songs', { n: m.item_count }) : t(`kind.${m.kind}`)}`}
										</span>
										{#if m.unmatched || m.pending || others.length || follows(m) || !m.mine || sorted || handsOff}
											<span class="m-tags">
												{#if !m.mine}<span class="tag accent"><Glyph name="user" size={11} weight={2.6} /> {m.user_name}</span>{/if}
												{#if sorted}<span class="tag accent rule-tag" title={ruleText(m.rule)}><Glyph name="filter" size={11} weight={2.6} /> <span class="clip">{ruleText(m.rule)}</span></span>{/if}
												{#if handsOff}<span class="tag">{t('member.handsOff')}</span>{/if}
												{#if follows(m)}<span class="tag">{t('member.follows')}</span>{/if}
												{#if m.unmatched}<span class="tag warn">{t('member.unmatched', { n: m.unmatched })}</span>{/if}
												{#if m.pending}<span class="tag">{t('member.pending', { n: m.pending })}</span>{/if}
												{#each others as o (o.id)}<span class="tag" title={t('member.alsoIn', { name: o.name })}><FadeBadge icon={fadeIcon(o)} color={o.color} size={14} /> {o.name}</span>{/each}
											</span>
										{/if}
									</span>
									{#if m.mine && m.initialized}
										<button class="gbtn round glass lq tt" onclick={() => ui.timeTravel({ account_id: m.account_id, kind: m.kind, collection_id: m.collection_id })} aria-label={t('tt.of', { name: m.collection_name })} title={t('tt.title')}>
											<Glyph name="history" size={17} weight={2.2} />
										</button>
									{/if}
									{#if fade.mine ? !m.mine : m.mine}
										<button class="role t-foot press" title={fade.mine ? t('share.theirDirection', { name: m.user_name }) : t('share.yourDirection')} aria-haspopup="menu" onclick={(e) => chooseRole(m, e.currentTarget)}>
											<Glyph name={roleIcon(m.role)} size={13} weight={2.4} />
											{t(`role.${m.role}`)}
											<Glyph name="updown" size={11} weight={2.4} />
										</button>
									{:else}
										<span class="role t-foot" title={t(`roleLong.${m.role}`)}>
											<Glyph name={roleIcon(m.role)} size={13} weight={2.4} />
											{t(`role.${m.role}`)}
										</span>
									{/if}
								</div>
							</li>
						{/each}
					</ol>
					{#if fade.last_summary?.unsorted}<p class="section-foot">{t('rule.waiting', { n: fade.last_summary.unsorted })}</p>{/if}
					{#if !fade.mine}<p class="section-foot">{t('share.guestHint', { name: owner })}</p>{/if}
				</section>

				{#if stats && stats.syncs && !liveView}
					<FadeStats {fade} {stats} />
				{/if}

				{#if fade.mine}
					<section class="section" aria-labelledby="share-h">
						<div class="section-head"><h2 id="share-h">{t('share.head')}</h2></div>
						<ul class="group">
							{#each guests as g (g.id)}
								<li>
									<div class="cell" style:--inset="18px">
										<span class="grow">
											<span class="clip block"><b>{g.name}</b></span>
											<span class="t-foot t-2 clip block">{t('n.collections', { n: g.n })}</span>
										</span>
										<button class="text-btn t-sub danger" onclick={() => removeGuest(g)}>{t('common.remove')}</button>
									</div>
								</li>
							{/each}
							{#each requests as inv (inv.id)}
								{@const r = inv.request!}
								<li>
									<div class="cell req" style:--inset="18px">
										<ServiceMark service={r.service} size={28} />
										<span class="grow">
											<span class="clip block">{t('share.wants', { name: inv.user.display_name })}</span>
											<span class="t-foot t-2 clip block">{r.create ? t('share.newList', { name: r.collection_name }) : r.collection_name} · {SERVICE_NAME[r.service]}</span>
										</span>
										<button class="role t-foot press" title={t('share.theirDirection', { name: inv.user.display_name })} aria-haspopup="menu" onclick={(e) => ((reqFor = inv), (reqBtn = e.currentTarget), (reqOpen = true))}>
											<Glyph name={roleIcon(askedRole(inv))} size={13} weight={2.4} />
											{t(`role.${askedRole(inv)}`)}
											<Glyph name="updown" size={11} weight={2.4} />
										</button>
									</div>
									<div class="req-actions">
										<button class="pb pb-small glass lq" onclick={() => reject(inv)}>{t('share.reject')}</button>
										<button class="pb pb-small glass tint lq" onclick={() => approve(inv)}>{t('share.approve')}</button>
									</div>
								</li>
							{/each}
							{#each waiting as inv (inv.id)}
								<li>
									<div class="cell" style:--inset="18px">
										<span class="grow">
											<span class="clip block">{inv.user.display_name}</span>
											<span class="t-foot t-2 clip block">{t('share.pending')}</span>
										</span>
										<button class="text-btn t-sub" onclick={() => cancelInvite(inv)}>{t('share.cancel')}</button>
									</div>
								</li>
							{/each}
							<li>
								{#if app.friends.friends.length}
									<button class="cell" bind:this={inviteBtn} onclick={() => (inviteOpen = true)} disabled={!invitable.length} aria-haspopup="menu" aria-expanded={inviteOpen}>
										<span class="grow tintc">{t('share.invite')}</span>
										<Glyph name="plus" size={18} weight={2.6} />
									</button>
								{:else}
									<button class="cell" onclick={() => ((ui.accountOpen = true), (ui.friendsOpen = true))}>
										<span class="grow t-2">{t('share.noFriends')}</span>
										<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
									</button>
								{/if}
							</li>
						</ul>
						<p class="section-foot">{requests.length ? t('share.requestHint') : app.friends.friends.length && !invitable.length ? t('share.allIn') : t('share.hint')}</p>
					</section>
				{/if}

				<div class="seg-wrap">
					<Segmented
						bind:value={tab}
						label={t('fade.view')}
						options={[
							{ value: 'changes', label: t('fade.changes') },
							{ value: 'history', label: t('fade.history') },
							{ value: 'songs', label: t('fade.songs') }
						]}
					/>
				</div>

				{#if tab === 'changes'}
					<section class="section">
						{#if !shownPlan}
							<div class="empty">
								<span class="e-ic"><Glyph name="eye" size={28} weight={1.8} /></span>
								<p class="t-sub t-2">{t('changes.empty')}</p>
								<button class="pb glass lq" onclick={() => runFade(fade.id, true)} disabled={!!fade.job || !!fault}><Glyph name="eye" size={18} weight={2.2} /> {t('fade.preview')}</button>
							</div>
						{:else}
							<p class="t-foot t-2 plan-note">
								{#if liveView}
									<span class="live-dot" aria-hidden="true"></span> {shownPlan.trigger === 'preview' ? t('changes.livePreview') : t('changes.liveRun')}
								{:else}
									{t(shownPlan.trigger === 'preview' ? 'changes.previewFrom' : 'changes.runFrom', { when: new Date(shownPlan.started_at * 1000).toLocaleString(i18n.locale, { dateStyle: 'medium', timeStyle: 'short' }) })}
								{/if}
							</p>
							<PlanList plan={shownPlan.plan} live={!!liveView} />
						{/if}
					</section>
				{:else if tab === 'history'}
					{#if liveView}<p class="t-foot t-2 plan-note pad"><span class="live-dot" aria-hidden="true"></span> {t('history.live')}</p>{/if}
					{#if !days.length}
						<div class="empty">
							<span class="e-ic"><Glyph name="note" size={28} weight={1.8} /></span>
							<p class="t-sub t-2">{t('history.empty')}</p>
						</div>
					{/if}
					{#each days as g (g.day)}
						<section class="section">
							<div class="section-head"><span class="caps">{g.day}</span></div>
							<ul class="group">
								{#each g.items as c (c.id)}
									<li class="cell ev" class:failed={!c.ok} class:fresh={c.id < 0} style:--inset="72px">
										<span class="ev-art">
											<Cover src={c.artwork} seed={c.label} size={44} song={c.song_id} />
											<span class="sign" class:minus={c.action === 'remove'} aria-label={c.action === 'add' ? t('plan.adds') : t('plan.removes')}>{c.action === 'add' ? '+' : '−'}</span>
										</span>
										<div class="grow">
											<p class="clip">{c.label}</p>
											<p class="t-foot t-2 clip where">
												{#if c.service}<ServiceMark service={c.service} size={13} title={false} />{/if}
												{c.collection_name ?? t('history.removedCollection')}{c.message ? ` · ${c.message}` : ''}
											</p>
											{#if c.note}<p class="t-foot note">{changeNote(c.note)}</p>{/if}
										</div>
										<span class="t-foot t-2">{time(c.ts)}</span>
									</li>
								{/each}
							</ul>
						</section>
					{/each}
				{:else}
					<div class="songs-head">
						<Segmented
							bind:value={songMode}
							small
							label={t('songs.mode')}
							options={[
								{ value: 'all', label: t('songs.all') },
								{ value: 'versions', label: audit?.length ? t('songs.versionsN', { n: audit.length }) : t('songs.versions') }
							]}
						/>
						{#if songMode === 'all'}
							<label class="field">
								<Glyph name="search" size={16} weight={2.2} />
								<input type="search" bind:value={songQuery} placeholder={t('songs.search')} aria-label={t('songs.search')} />
							</label>
						{/if}
					</div>
					{#if songMode === 'all'}
						<div class="section">
						<p class="t-foot t-2 count">{t('n.songs', { n: songs.total })}</p>
						<ul class="group songs">
							{#each songs.items as s (s.id)}
								<li>
									<button class="cell" style:--inset="74px" onclick={() => ui.showSong(fade.id, s.id)}>
										<Cover src={s.artwork} seed={String(s.id)} size={44} song={s.id} />
										<span class="grow">
											<span class="clip block">{s.title}</span>
											<span class="t-foot t-2 clip block">{s.artist}</span>
										</span>
										<span class="states">
											{#each s.at as a (a.member_id)}
												{@const m = memberById.get(a.member_id)}
												<span class="st {a.state}" title="{m?.collection_name}: {t(STATE_KEY[a.state as keyof typeof STATE_KEY])}">
													{#if m}<ServiceMark service={m.service} size={14} title={false} />{/if}
												</span>
											{/each}
										</span>
									</button>
								</li>
							{/each}
						</ul>
						<p class="section-foot legend">
							<span class="st ok"></span> {t('songs.ok')} <span class="st pending"></span> {t('songs.pending')} <span class="st excluded"></span> {t('songs.excluded')} <span class="st missing"></span> {t('songs.missing')}
						</p>
						</div>
					{:else if audit === null}
						<p class="t-sub t-2 pad">{t('songs.checking')}</p>
					{:else if !audit.length}
						<div class="empty">
							<span class="e-ic good"><Glyph name="check" size={28} weight={2.2} /></span>
							<p class="t-sub t-2">{t('songs.allSame')}</p>
						</div>
					{:else}
						<div class="section">
						<p class="t-foot t-2 count">{t('songs.versionsHint')}</p>
						<ul class="group songs">
							{#each audit as e (e.song_id)}
								<li>
									<button class="cell" style:--inset="74px" onclick={() => ui.showSong(fade.id, e.song_id)}>
										<Cover src={e.origin.artwork} seed={e.origin.id} size={44} track={e.origin} song={e.song_id} />
										<span class="grow">
											<span class="clip block">{e.origin.title}</span>
											<span class="t-foot t-2 clip block">{e.origin.artist}</span>
											<span class="m-tags">
												{#each e.stops as st (st.member_id)}<span class="tag warn"><ServiceMark service={st.service} size={12} title={false} /> {st.notes.join(', ')}</span>{/each}
											</span>
										</span>
										<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
									</button>
								</li>
							{/each}
						</ul>
						</div>
					{/if}
				{/if}

				{#if ignored.length}
					<section class="section">
						<div class="group">
							<button class="cell" onclick={() => (showIgnored = !showIgnored)} aria-expanded={showIgnored}>
								<span class="grow t-2">{t('issue.ignoredN', { n: ignored.length })}</span>
								<span class="chev" class:up={showIgnored}><Glyph name="chevronDown" size={18} weight={2.4} /></span>
							</button>
							{#if showIgnored}
								{#each ignored as i (i.id)}
									<div class="cell" style:--inset="18px">
										<span class="grow">
											<span class="clip block">{i.data.track?.title}</span>
											<span class="t-foot t-2 clip block">{i.data.track?.artist} · {i.collection_name}</span>
										</span>
										<button class="gbtn glass lq small-btn" onclick={async () => (await restoreIssue(id, i)) && reload()}>{t('issue.restore')}</button>
									</div>
								{/each}
							{/if}
						</div>
					</section>
				{/if}

				<section class="section" aria-labelledby="plan-h" style="margin-top: 32px">
					<div class="section-head"><h2 id="plan-h">{t('editor.schedule')}</h2></div>
					<div class="group" class:readonly={!fade.mine} inert={!fade.mine}>
						<button class="cell" onclick={() => ui.edit(fade.id)}>
							<span class="grow">{t('editor.runs')}</span><span class="value">{fade.paused ? t('status.paused') : intervalLabel(fade.interval_minutes)}</span><span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
						<button class="cell" onclick={() => ui.edit(fade.id)}>
							<span class="grow">{t('fade.deletes')}</span><span class="value">{fade.sync_deletions ? t('fade.deletesOn') : t('fade.deletesOff')}</span><span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
						{#if fade.sync_deletions}
							<button class="cell" onclick={() => ui.edit(fade.id)}>
								<span class="grow">{t('fade.guard')}</span><span class="value">{fade.delete_confirm ? t('fade.guardValue', { n: fade.delete_guard_min, pct: Math.round(fade.delete_guard_ratio * 100) }) : t('fade.guardOff')}</span><span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
							</button>
						{/if}
					</div>
				</section>
			</div>
		</Page>

		<Menu bind:open={inviteOpen} anchor={inviteBtn} label={t('share.pickFriend')} items={invitable.map((p) => ({ label: p.display_name, run: () => invite(p) }))} />
		<Menu
			bind:open={roleOpen}
			anchor={roleBtn}
			label={fade.mine && roleFor ? t('share.theirDirection', { name: roleFor.user_name }) : t('share.yourDirection')}
			items={(['send', 'both', 'receive'] as Role[]).map((r) => ({ label: t(`role.${r}`), checked: roleFor?.role === r, run: () => roleFor && setRole(roleFor, r) }))}
		/>
		<Menu
			bind:open={reqOpen}
			anchor={reqBtn}
			label={reqFor ? t('share.theirDirection', { name: reqFor.user.display_name }) : ''}
			items={(['send', 'both', 'receive'] as Role[]).map((r) => ({ label: t(`role.${r}`), checked: !!reqFor && askedRole(reqFor) === r, run: () => reqFor && (reqRole[reqFor.id] = r) }))}
		/>
		<IssueSheet bind:open={pickOpen} bind:issue={picking} fadeId={fade.id} color={fade.color} onchange={reload} />
	</div>
{:else if app.linesLoaded}
	<Page title={t('fade.one')} {back}>
		<div class="empty">
			<span class="e-ic"><Glyph name="fades" size={28} weight={1.8} /></span>
			<p class="t-title3">{t('fade.goneTitle')}</p>
			<p class="t-sub t-2">{t('fade.goneText')}</p>
		</div>
	</Page>
{/if}

<style>
	.wrap {
		position: absolute;
		inset: 0;
		--cover: 180px;
	}
	.block {
		display: block;
	}

	/* ------------------------------------------------ hero */
	.hero {
		position: relative;
		isolation: isolate;
		display: grid;
		justify-items: center;
		gap: 18px;
		margin-top: calc(-1 * (var(--safe-top) + var(--bar)));
		padding: calc(var(--safe-top) + var(--bar) + 8px) 20px 24px;
		text-align: center;
	}
	.meta {
		display: grid;
		justify-items: center;
		gap: 3px;
		width: 100%;
	}
	.eyebrow {
		display: flex;
		align-items: center;
		gap: 6px;
		color: var(--tint);
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.sub {
		color: var(--label-2);
	}
	.status {
		display: flex;
		align-items: center;
		gap: 6px;
		color: var(--label-2);
	}
	.dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--green);
	}
	.pulse {
		background: var(--tint-fill);
		animation: pulse 1.2s ease-in-out infinite alternate;
	}
	@keyframes pulse {
		to {
			opacity: 0.25;
		}
	}
	.cta {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: 1fr;
		gap: 12px;
		width: 100%;
		max-width: 420px;
		margin-top: 16px;
	}
	.cta .pb:not(.tint) {
		color: var(--label);
	}

	.content {
		padding-top: 4px;
	}

	/* ------------------------------------------------ cards */
	.card {
		margin: 0 16px 24px;
		padding: 14px 16px;
		border-radius: var(--r-group);
		background: var(--cell-grouped);
		-webkit-backdrop-filter: blur(20px) saturate(1.6);
		backdrop-filter: blur(20px) saturate(1.6);
		box-shadow:
			var(--group-shadow),
			inset 0 0 0 0.5px var(--rim-edge),
			var(--group-rim);
	}
	.progress .p-row {
		display: flex;
		justify-content: space-between;
		margin-bottom: 10px;
	}
	.bar {
		display: block;
		height: 6px;
		border-radius: 3px;
		background: var(--fill-3);
		overflow: hidden;
	}
	.fill {
		display: block;
		height: 100%;
		border-radius: 3px;
		background: var(--tint-fill);
		transition: width 160ms linear;
	}
	.fault {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.fault .grow {
		flex: 1;
		display: grid;
		gap: 2px;
	}

	/* ------------------------------------------------ route: the line runs through its stops */
	.route {
		list-style: none;
	}
	.stop {
		position: relative;
	}
	.stop .cell {
		padding-left: 52px;
		min-height: 64px;
	}
	.rail {
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 44px;
		z-index: 1;
		pointer-events: none;
	}
	.rseg {
		position: absolute;
		left: 21px;
		width: 6px;
		background: var(--tint-fill);
	}
	.rseg.up {
		top: 0;
		height: 50%;
	}
	.rseg.down {
		top: 50%;
		bottom: 0;
	}
	.first .rseg.up,
	.last .rseg.down {
		display: none;
	}
	.node {
		position: absolute;
		left: 15px;
		top: 50%;
		width: 18px;
		height: 18px;
		margin-top: -9px;
		border-radius: 50%;
		background: var(--cell-grouped);
		border: 5px solid var(--tint-fill);
	}
	.flow {
		position: absolute;
		left: 16px;
		bottom: -8px;
		display: grid;
		place-items: center;
		width: 16px;
		height: 16px;
		border-radius: 50%;
		background: var(--tint-fill);
		color: var(--on-tint);
		z-index: 2;
	}
	.flow.right {
		transform: rotate(90deg);
	}
	.flow.left {
		transform: rotate(-90deg);
	}
	.flow.both {
		transform: rotate(90deg);
	}
	.readonly :global(.chev) {
		display: none;
	}
	.tt {
		flex: none;
		width: 32px;
		height: 32px;
		min-width: 32px;
		min-height: 32px;
		padding: 0;
	}
	.role {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		flex: none;
		padding: 4px 9px;
		border-radius: 999px;
		background: var(--ctl);
		box-shadow: var(--ctl-shadow);
		color: var(--tint);
		font-weight: 600;
	}
	.req {
		gap: 12px;
	}
	.req-actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding: 0 16px 12px 58px;
	}

	/* ------------------------------------------------ songs & history */
	.seg-wrap {
		margin: 8px 16px 14px;
	}
	.ev {
		align-items: flex-start;
	}
	.ev .grow {
		display: grid;
		gap: 2px;
	}

	/* ------------------------------------------------ pick sheet */

	@media (min-width: 721px) {
		.wrap {
			--cover: 220px;
		}
		.hero {
			grid-template-columns: auto 1fr;
			justify-items: start;
			align-items: end;
			gap: 28px;
			padding: 24px 36px 36px;
			margin-top: calc(-1 * var(--bar));
			text-align: left;
		}
		.meta {
			justify-items: start;
		}
		.meta h1 {
			font-size: 2.25rem;
			letter-spacing: var(--tr-large);
		}
		.cta {
			width: auto;
			grid-auto-columns: auto;
			justify-content: start;
		}
		.content {
			max-width: calc(var(--content-max) + 72px);
		}
		.content .section,
		.card {
			margin-left: 36px;
			margin-right: 36px;
		}
		.seg-wrap {
			margin: 8px 36px 14px;
			max-width: 420px;
		}
	}
	.col {
		display: grid;
		justify-items: end;
		gap: 2px;
	}
	.notice {
		margin: 0 16px 20px;
		padding: 10px 14px;
		border-radius: 18px;
		background: color-mix(in srgb, var(--orange) 14%, transparent);
		color: var(--orange-text);
	}
	.m-tags {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 5px;
	}
	.rule-tag {
		max-width: 100%;
		min-width: 0;
	}
	.empty {
		display: grid;
		justify-items: center;
		gap: 10px;
		padding: 34px 32px;
		text-align: center;
	}
	.e-ic {
		color: var(--label-3);
	}
	.e-ic.good {
		color: var(--green);
	}
	.plan-note {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 0 6px 12px;
	}
	.pad {
		padding: 0 22px 12px;
	}
	.live-dot {
		flex: none;
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--tint-fill);
		animation: pulse 1.2s ease-in-out infinite alternate;
	}
	.ev-art {
		position: relative;
		flex: none;
	}
	.sign {
		position: absolute;
		right: -5px;
		bottom: -5px;
		display: grid;
		place-items: center;
		width: 20px;
		height: 20px;
		border-radius: 50%;
		background: var(--green);
		color: #fff;
		font-weight: 800;
		font-size: 13px;
		line-height: 1;
		box-shadow: 0 0 0 2px var(--bg);
	}
	.sign.minus {
		background: var(--red);
	}
	.failed p:first-child {
		text-decoration: line-through;
		color: var(--label-2);
	}
	.fresh {
		animation: fresh 300ms ease-out;
	}
	@keyframes fresh {
		from {
			opacity: 0;
			transform: translateY(-4px);
		}
	}
	.where {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.note {
		color: var(--orange-text);
	}
	.songs-head {
		display: grid;
		gap: 10px;
		margin: 0 16px 10px;
	}
	.count {
		padding: 0 18px 6px;
	}
	.states {
		display: flex;
		gap: 4px;
	}
	.st {
		display: inline-grid;
		place-items: center;
		width: 22px;
		height: 22px;
		border-radius: 7px;
		vertical-align: middle;
	}
	.st.ok {
		background: color-mix(in srgb, var(--green) 22%, transparent);
	}
	.st.pending {
		background: color-mix(in srgb, var(--orange) 24%, transparent);
	}
	.st.excluded {
		background: var(--fill);
		opacity: 0.6;
	}
	.st.missing {
		background: color-mix(in srgb, var(--red) 18%, transparent);
		opacity: 0.7;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		padding: 10px 22px 0;
	}
	.legend .st {
		width: 12px;
		height: 12px;
		border-radius: 4px;
	}
	.small-btn {
		height: 34px;
		font-size: var(--f-sub);
	}
	.chev.up {
		transform: rotate(180deg);
	}
	.indet {
		animation: indet 1.2s ease-in-out infinite alternate;
	}
	@keyframes indet {
		from {
			margin-left: 0;
		}
		to {
			margin-left: 70%;
		}
	}
	@media (min-width: 721px) {
		.songs-head {
			grid-template-columns: auto 1fr;
			margin: 0 36px 10px;
			max-width: var(--content-max);
		}
		.notice {
			margin-left: 36px;
			margin-right: 36px;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.live-dot,
		.fresh,
		.indet,
		.pulse {
			animation: none;
		}
	}
</style>
