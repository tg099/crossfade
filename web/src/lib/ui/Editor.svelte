<script lang="ts" module>
	import type { Kind, Role, Service, SortRule } from '#lib/types.ts';
	export interface DraftStop {
		uid: number;
		service: Service;
		kind: Kind | null;
		collection_id: string;
		collection_name: string;
		role: Role;
		follow: boolean; // send-only: still apply deletions made elsewhere
		create: boolean;
		/** receiving: only the songs that fit (sorting into playlists); null = every song */
		rule: SortRule | null;
		/** sending: let go of songs once they are sorted (an inbox like liked songs) */
		move: boolean;
	}
	interface Draft {
		name: string;
		color: string;
		icon: string;
		stops: DraftStop[];
		interval: number;
		deletions: boolean;
		guardMin: number;
		guardPct: number;
		paused: boolean;
		versionPolicy: 'closest' | 'exact';
		/** built from the sorting template: new collections receive and get a rule */
		sorting?: boolean;
	}
	// drafts survive swiping the sheet away; only "Cancel" throws them out
	const drafts = new Map<number, Draft>();
	let uid = 0;
</script>

<script lang="ts">
	import ServiceMark from '#lib/components/ServiceMark.svelte';
	import { LINE_COLORS, SERVICE_NAME } from '#lib/format.ts';
	import { api, post, put } from '#lib/api.ts';
	import { app, memberKey } from '#lib/state.svelte.ts';
	import type { CollectionInfo, Line } from '#lib/types.ts';
	import ActionSheet from './ActionSheet.svelte';
	import FadeBadge from './FadeBadge.svelte';
	import Glyph from './Glyph.svelte';
	import Menu from './Menu.svelte';
	import Mosaic from './Mosaic.svelte';
	import Segmented from './Segmented.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import Switch from './Switch.svelte';
	import SwipeRow from './SwipeRow.svelte';
	import { askDeleteFade } from './ctx.ts';
	import { COLOR_KEY, FADE_ICONS, fadeCovers, fadeIcon, ruleText } from './fade.ts';
	import { t, type Key } from './i18n.svelte.ts';
	import { Spring } from './motion.ts';
	import { Reorder } from './reorder.svelte.ts';
	import { tintVars } from './tint.ts';
	import { flowIcon, intervalLabel, intervals, nav, ui } from './ui.svelte.ts';

	let d = $state<Draft>(blank());
	let original = '';
	let restored = $state(false);
	let busy = $state(false);
	const editId = $derived(ui.editing ?? 0);
	const fade = $derived(editId ? app.line(editId) : undefined);
	const connected = $derived(app.connected.filter((s) => s.status !== 'error').map((s) => s.service));
	const LIKED: Record<Service, Key> = { ytm: 'liked.ytm', apple: 'liked.apple', spotify: 'liked.spotify', tidal: 'liked.tidal', deezer: 'liked.deezer' };

	function blank(): Draft {
		const used = new Set(app.lines.map((f) => f.color.toUpperCase()));
		const usedIcons = new Set(app.lines.map((f) => f.icon));
		return {
			name: '',
			color: LINE_COLORS.find((c) => !used.has(c.hex))?.hex ?? LINE_COLORS[0].hex,
			icon: FADE_ICONS.find((i) => !usedIcons.has(i)) ?? 'note',
			stops: [],
			interval: 60,
			deletions: true,
			guardMin: 5,
			guardPct: 20,
			paused: false,
			versionPolicy: 'closest'
		};
	}
	function fromFade(f: Line): Draft {
		return {
			name: f.name,
			color: f.color,
			icon: fadeIcon(f),
			stops: f.members.filter((m) => m.mine).map((m) => ({ uid: ++uid, service: m.service, kind: m.kind, collection_id: m.collection_id, collection_name: m.collection_name, role: m.role, follow: m.follow_deletions, create: false, rule: m.rule, move: m.move_sorted })),
			interval: f.interval_minutes,
			deletions: f.sync_deletions,
			guardMin: f.delete_guard_min,
			guardPct: Math.round(f.delete_guard_ratio * 100),
			paused: f.paused,
			versionPolicy: f.version_policy
		};
	}
	function fromTemplate(): Draft {
		const b = blank();
		if (ui.editorTemplate === 'likes') {
			b.name = t('start.likes');
			b.icon = 'heart';
			b.stops = connected.map((s) => ({ uid: ++uid, service: s, kind: 'liked', collection_id: '', collection_name: t(LIKED[s]), role: 'both', follow: false, create: false, rule: null, move: false }));
		} else if (ui.editorTemplate === 'copy' && connected.length >= 2) {
			b.name = t('start.copy');
			b.icon = 'car';
			b.stops = [
				{ uid: ++uid, service: connected[0], kind: null, collection_id: '', collection_name: '', role: 'send', follow: false, create: false, rule: null, move: false },
				{ uid: ++uid, service: connected[1], kind: null, collection_id: '', collection_name: '', role: 'receive', follow: false, create: false, rule: null, move: false }
			];
		} else if (ui.editorTemplate === 'sort' && connected.length) {
			// liked songs as the inbox; the playlists to sort into are added next, each with its rule
			b.name = t('start.sort');
			b.icon = 'star';
			b.sorting = true;
			b.stops = [{ uid: ++uid, service: connected[0], kind: 'liked', collection_id: '', collection_name: t(LIKED[connected[0]]), role: 'send', follow: false, create: false, rule: null, move: false }];
		}
		return b;
	}

	// load when the sheet opens
	let loadedFor = '';
	$effect(() => {
		if (!ui.editorOpen) return;
		const id = ui.editing ?? 0;
		const key = `${id}:${ui.editorTemplate ?? ''}`;
		if (loadedFor === key) return;
		loadedFor = key;
		const f = id ? app.line(id) : undefined;
		const base = f ? fromFade(f) : fromTemplate();
		original = JSON.stringify(base);
		const saved = !ui.editorTemplate ? drafts.get(id) : undefined;
		restored = !!saved;
		d = saved ? structuredClone(saved) : base;
		nameTouched = false;
	});

	const dirty = $derived(JSON.stringify(d) !== original);

	function closed() {
		if (dirty && !ui.editorTemplate) drafts.set(editId, $state.snapshot(d) as Draft);
		else drafts.delete(editId);
		loadedFor = '';
		ui.editing = null;
		ui.editorTemplate = null;
	}

	// ---------------------------------------------------------------- checks (inline, while editing)
	const accountOf = (s: Service) => app.services.find((x) => x.service === s)?.account_id ?? null;
	const complete = $derived(d.stops.filter((s) => s.kind));
	/** friends' collections in this fade: theirs to manage, but they count */
	const friendStops = $derived(fade?.members.filter((m) => !m.mine) ?? []);
	const problems = $derived.by(() => {
		const p: string[] = [];
		const all = [...complete.map((s) => ({ role: s.role, follow: s.follow })), ...friendStops.map((m) => ({ role: m.role, follow: m.follow_deletions }))];
		// one own collection is fine: a friend can bring the second one (see `soloHint`)
		if (!complete.length) p.push(t('editor.needTwo'));
		else if (all.length >= 2) {
			if (!all.some((s) => s.role !== 'receive')) p.push(t('editor.noSender'));
			if (!all.some((s) => s.role !== 'send' || (s.follow && d.deletions))) p.push(t('editor.noReceiver'));
		}
		if (d.stops.some((s) => !s.kind)) p.push(t('editor.pickMissing'));
		return p;
	});
	const warnings = $derived.by(() => {
		const w: string[] = [];
		if (!d.deletions) return w;
		for (const s of complete) {
			if ((s.role === 'send' && !s.follow) || s.create) continue;
			const acc = accountOf(s.service);
			if (acc === null) continue;
			const key = memberKey({ account_id: acc, kind: s.kind!, collection_id: s.collection_id });
			for (const o of app.interchanges.get(key) ?? []) {
				if (o.id === fade?.id) continue;
				const ol = app.line(o.id);
				const om = ol?.members.find((m) => memberKey(m) === key);
				if (ol?.sync_deletions && om && (om.role !== 'send' || om.follow_deletions)) w.push(t('editor.interchange', { name: s.collection_name, other: o.name }));
			}
		}
		return w;
	});
	const soloHint = $derived(complete.length === 1 && !friendStops.length);
	const canSave = $derived(!!d.name.trim() && problems.length === 0 && !busy);
	let nameTouched = $state(false);

	async function save() {
		if (!canSave) return;
		busy = true;
		const body = {
			name: d.name.trim(),
			color: d.color,
			icon: d.icon,
			sync_deletions: d.deletions,
			interval_minutes: d.interval,
			delete_guard_min: d.guardMin,
			delete_guard_ratio: d.guardPct / 100,
			paused: d.paused,
			version_policy: d.versionPolicy,
			members: d.stops.map((s) => ({
				service: s.service,
				kind: s.kind,
				collection_id: s.collection_id,
				collection_name: s.collection_name,
				role: s.role,
				follow_deletions: s.role === 'send' && s.follow,
				create: s.create,
				rule: s.role !== 'send' ? s.rule : null,
				move_sorted: s.role !== 'receive' && s.move && sorts
			}))
		};
		try {
			const saved = fade ? await put<Line>(`/api/lines/${fade.id}`, body) : await post<Line>('/api/lines', body);
			await app.loadLines();
			original = JSON.stringify(d);
			drafts.delete(editId);
			ui.editorOpen = false;
			ui.say(fade ? t('toast.saved') : t('editor.created', { name: saved.name }));
			if (!fade) setTimeout(() => nav.openFade(saved.id), 350);
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = false;
		}
	}

	let confirmDiscard = $state(false);
	function cancel() {
		if (dirty) confirmDiscard = true;
		else ui.editorOpen = false;
	}
	function discard() {
		drafts.delete(editId);
		d = JSON.parse(original);
		ui.editorOpen = false;
	}
	function askDelete() {
		if (!fade) return;
		const id = fade.id;
		askDeleteFade(id, () => {
			drafts.delete(id);
			ui.editorOpen = false;
		});
	}

	// ---------------------------------------------------------------- interval menu
	let intervalBtn: HTMLButtonElement | undefined = $state();
	let intervalOpen = $state(false);

	// ---------------------------------------------------------------- order of the collections
	const order = new Reorder(
		() => d.stops.map((s) => s.uid),
		(from, to) => {
			const [s] = d.stops.splice(from, 1);
			d.stops.splice(to, 0, s);
		}
	);

	// ---------------------------------------------------------------- add / choose a collection (stacked sheet)
	let pickOpen = $state(false);
	let pickFor = $state<number | null>(null); // a stop without collection (template), or null = add new
	let pickSvc = $state<Service | null>(null);
	let pane = $state(0);
	const paneSpring = new Spring(0, (v) => (pane = v), 0.002);
	let fresh = $state<number | null>(null);
	let collections = $state<Partial<Record<Service, CollectionInfo[]>>>({});
	let colLoading = $state(false);
	let colError = $state('');
	let colFilter = $state('');

	async function loadCollections(s: Service, refresh = false) {
		if (collections[s] && !refresh) return;
		colLoading = true;
		colError = '';
		try {
			collections[s] = await api<CollectionInfo[]>(`/api/services/${s}/collections${refresh ? '?refresh=true' : ''}`);
		} catch (e) {
			colError = (e as Error).message;
		} finally {
			colLoading = false;
		}
	}
	function openPicker(forUid: number | null = null, service: Service | null = null) {
		pickFor = forUid;
		colFilter = '';
		if (service) {
			pickSvc = service;
			paneSpring.set(1);
			loadCollections(service);
		} else {
			pickSvc = null;
			paneSpring.set(0);
		}
		pickOpen = true;
	}
	function chooseSvc(s: Service) {
		pickSvc = s;
		colFilter = '';
		loadCollections(s);
		paneSpring.to(1, { response: 0.38 });
	}
	function choose(c: { kind: Kind; id: string; name: string }, create = false) {
		if (!pickSvc) return;
		const props = { service: pickSvc, kind: c.kind, collection_id: c.id, collection_name: c.name, create };
		const target = pickFor !== null ? d.stops.find((x) => x.uid === pickFor) : null;
		if (target) Object.assign(target, props);
		else {
			const id = ++uid;
			// in a sorting fade a new playlist is somewhere to sort into: it receives, and its rule comes next
			const into = d.sorting && c.kind === 'playlist';
			d.stops.push({ uid: id, role: create || into ? 'receive' : 'both', follow: false, rule: null, move: false, ...props });
			fresh = id;
			pickOpen = false;
			if (into) setTimeout(() => openRule(id), 380);
			return;
		}
		pickOpen = false;
	}
	const taken = (s: Service, c: CollectionInfo) => d.stops.some((x) => x.service === s && x.kind === c.kind && x.collection_id === c.id && x.uid !== pickFor);
	const shownCollections = $derived.by(() => {
		const q = colFilter.trim().toLowerCase();
		return (pickSvc ? (collections[pickSvc] ?? []) : []).filter((c) => !q || c.name.toLowerCase().includes(q));
	});
	const newName = $derived.by(() => {
		// An unnamed fade can already have a source collection, e.g. “BEATZ”.
		// Prefer a sender and ignore new playlists so their placeholder never becomes the source.
		const existing = d.stops.filter((s) => s.kind && !s.create && s.collection_name.trim());
		const source = existing.find((s) => s.role !== 'receive') ?? existing[0];
		return colFilter.trim() || d.name.trim() || source?.collection_name.trim() || t('editor.newPlaylist');
	});
	const ROLE_OPTS = $derived<{ value: Role; label: string }[]>([
		{ value: 'send', label: t('role.send') },
		{ value: 'both', label: t('role.both') },
		{ value: 'receive', label: t('role.receive') }
	]);
	const kindLabel = (k: Kind) => t(`kind.${k}`);

	// ---------------------------------------------------------------- sorting into playlists
	/** some collection of this fade only takes what fits its rule */
	const sorts = $derived(d.stops.some((s) => s.role !== 'send' && s.rule) || !!d.sorting);
	const ENERGY: { value: 'low' | 'medium' | 'high'; key: Key }[] = [
		{ value: 'low', key: 'rule.energy.low' },
		{ value: 'medium', key: 'rule.energy.medium' },
		{ value: 'high', key: 'rule.energy.high' }
	];

	type RuleMode = 'all' | 'rule' | 'rest';
	let ruleOpen = $state(false);
	let ruleFor = $state<number | null>(null);
	let ruleMode = $state<RuleMode>('all');
	let rf = $state({ moods: '', genres: '', tags: '', artists: '', languages: '' });
	let energy = $state<('low' | 'medium' | 'high')[]>([]);
	type Label = { label: string; songs: number };
	let vocab = $state<{ moods: Label[]; genres: Label[]; tags: Label[] } | null>(null);
	const ruleStop = $derived(ruleFor !== null ? d.stops.find((x) => x.uid === ruleFor) : undefined);
	const split = (v: string) => [...new Set(v.split(',').map((x) => x.trim()).filter(Boolean))];

	function openRule(id: number) {
		const st = d.stops.find((x) => x.uid === id);
		if (!st) return;
		const r = st.rule;
		ruleFor = id;
		ruleMode = !r ? 'all' : r.rest ? 'rest' : 'rule';
		rf = { moods: r?.moods.join(', ') ?? '', genres: r?.genres.join(', ') ?? '', tags: r?.tags.join(', ') ?? '', artists: r?.artists.join(', ') ?? '', languages: r?.languages.join(', ') ?? '' };
		energy = [...(r?.energy ?? [])];
		ruleOpen = true;
		if (!vocab) api<typeof vocab>('/api/mcp/labels').then((v) => (vocab = v)).catch(() => {});
	}
	const draftRule = $derived<SortRule>({ moods: split(rf.moods), genres: split(rf.genres), tags: split(rf.tags), artists: split(rf.artists), languages: split(rf.languages), energy, rest: false });
	const ruleEmpty = $derived(ruleMode === 'rule' && !Object.values(draftRule).some((v) => Array.isArray(v) && v.length));
	function saveRule() {
		if (!ruleStop || ruleEmpty) return;
		ruleStop.rule = ruleMode === 'all' ? null : ruleMode === 'rest' ? { moods: [], genres: [], tags: [], artists: [], languages: [], energy: [], rest: true } : $state.snapshot(draftRule) as SortRule;
		ruleOpen = false;
	}
	/** tap a label you already use: it joins the line (or leaves it again) */
	function toggleLabel(key: 'moods' | 'genres' | 'tags', label: string) {
		const list = split(rf[key]);
		rf[key] = (list.some((x) => x.toLowerCase() === label) ? list.filter((x) => x.toLowerCase() !== label) : [...list, label]).join(', ');
	}
	const hasLabel = (key: 'moods' | 'genres' | 'tags', label: string) => split(rf[key]).some((x) => x.toLowerCase() === label);
</script>

<Sheet bind:open={ui.editorOpen} label={fade ? t('editor.edit') : t('editor.new')} class="tinted editor" style={tintVars(d.color)} onclosed={closed}>
	{#snippet header()}
		<SheetBar
			title={fade ? t('editor.edit') : t('editor.new')}
			leading={{ label: t('common.cancel'), run: cancel }}
			confirm={{ label: fade ? t('common.done') : t('editor.create'), run: save, disabled: !canSave }}
		/>
	{/snippet}

	<div class="form">
		{#if restored && dirty}
			<p class="restored t-foot">{t('editor.draft')} <button class="text-btn t-foot" onclick={discard}>{t('editor.discardShort')}</button></p>
		{/if}

		<!-- live preview of the fade -->
		<div class="preview" aria-hidden="true">
			<span class="pv-cover">
				<Mosaic covers={fade ? fadeCovers(fade) : []} seed={d.name || 'new'} color={d.color} size={76} />
				<span class="pv-badge"><FadeBadge icon={d.icon} color={d.color} size={30} /></span>
			</span>
			<div class="pv-text">
				<p class="pv-name clip">{d.name.trim() || t('editor.new')}</p>
				<p class="pv-route">
					{#each d.stops as s, i (s.uid)}
						{#if i}<span class="pv-arr"><Glyph name={flowIcon(d.stops[i - 1], s)} size={13} weight={2.6} /></span>{/if}
						<ServiceMark service={s.service} size={18} title={false} />
					{:else}
						<span class="t-foot t-2">{t('editor.noStops')}</span>
					{/each}
				</p>
			</div>
		</div>

		<div class="section">
			<div class="group">
				<label class="cell">
					<span class="lbl">{t('editor.name')}</span>
					<input class="grow tinput" bind:value={d.name} maxlength="60" placeholder={t('editor.namePh')} onblur={() => (nameTouched = true)} enterkeyhint="done" />
				</label>
			</div>
			{#if nameTouched && !d.name.trim()}<p class="section-foot warn">{t('editor.needName')}</p>{/if}
		</div>

		<div class="section">
			<div class="section-head"><span class="caps">{t('editor.look')}</span></div>
			<div class="group">
				<div class="swatches" role="radiogroup" aria-label={t('editor.color')}>
					{#each LINE_COLORS as c}
						<button type="button" class="sw" role="radio" aria-checked={d.color.toUpperCase() === c.hex} aria-label={t(COLOR_KEY[c.hex] ?? 'editor.color')} style:--c={c.hex} onclick={() => (d.color = c.hex)}></button>
					{/each}
				</div>
				<div class="icons" role="radiogroup" aria-label={t('editor.icon')}>
					{#each FADE_ICONS as ic}
						<button type="button" class="ic" role="radio" aria-checked={d.icon === ic} aria-label={t(`icon.${ic}` as Key)} onclick={() => (d.icon = ic)}>
							{#if d.icon === ic}
								<FadeBadge icon={ic} color={d.color} size={34} />
							{:else}
								<Glyph name={ic} size={22} weight={2} />
							{/if}
						</button>
					{/each}
				</div>
			</div>
		</div>

		<div class="section">
			<div class="section-head"><span class="caps">{t('editor.collections')}</span></div>
			<ul class="group stops">
				{#each d.stops as s, i (s.uid)}
					<li class:lifted={order.dragId === s.uid} class:appear={fresh === s.uid} style:transform={order.style(s.uid)}>
						<SwipeRow actions={[{ label: t('common.remove'), tone: 'red', run: () => (d.stops = d.stops.filter((x) => x.uid !== s.uid)) }]}>
							<div class="cell stop" style:--inset="58px">
								<ServiceMark service={s.service} size={30} />
								<div class="grow">
									<div class="nm">
									{#if s.kind}
										<p class="clip">{s.collection_name}</p>
										<p class="t-foot t-2 clip">{SERVICE_NAME[s.service]} · {s.create ? t('editor.newPlaylist') : kindLabel(s.kind)}</p>
									{:else}
										<button class="text-btn choose" onclick={() => openPicker(s.uid, s.service)}>{t('editor.chooseCollection', { service: SERVICE_NAME[s.service] })}</button>
									{/if}
									</div>
									<div class="role">
										<Segmented bind:value={s.role} options={ROLE_OPTS} label={t('editor.direction', { name: s.collection_name || SERVICE_NAME[s.service] })} small />
									</div>
									{#if s.role === 'send' && d.deletions}
										<div class="follow">
											<span class="grow-text">
												<span class="fl">{t('editor.follow')}</span>
												<span class="fl t-foot t-2">{t('editor.followHint')}</span>
											</span>
											<Switch bind:checked={s.follow} label={t('editor.followFor', { name: s.collection_name || SERVICE_NAME[s.service] })} />
										</div>
									{/if}
									{#if s.kind && s.role !== 'send'}
										<!-- what comes here: every song, or only what fits a rule (sorting into playlists) -->
										<button class="rule" class:set={!!s.rule} onclick={() => openRule(s.uid)} aria-label={t('rule.editFor', { name: s.collection_name })}>
											<Glyph name="filter" size={14} weight={2.3} />
											<span class="clip">{ruleText(s.rule)}</span>
											<Glyph name="chevronRight" size={13} weight={2.6} />
										</button>
									{/if}
									{#if s.kind && s.role !== 'receive' && sorts}
										<div class="follow move">
											<span class="grow-text">
												<span class="fl">{t('rule.move')}</span>
												<span class="fl t-foot t-2">{t('rule.moveHint')}</span>
											</span>
											<Switch bind:checked={s.move} label={t('rule.moveFor', { name: s.collection_name || SERVICE_NAME[s.service] })} />
										</div>
									{/if}
								</div>
								<button
									class="grip"
									data-grip={s.uid}
									aria-label={t('editor.move', { name: s.collection_name || SERVICE_NAME[s.service] })}
									onpointerdown={(e) => order.down(e, i)}
									onpointermove={order.move}
									onpointerup={order.up}
									onpointercancel={order.up}
									onkeydown={(e) => order.key(e, i, (id) => `[data-grip="${id}"]`)}
								>
									<Glyph name="grip" size={22} weight={1.8} />
								</button>
							</div>
						</SwipeRow>
					</li>
				{/each}
				<li>
					<button class="cell add" style:--inset="58px" onclick={() => openPicker()}>
						<span class="add-ic"><Glyph name="plus" size={18} weight={2.6} /></span>
						<span class="grow">{t('editor.addCollection')}</span>
					</button>
				</li>
			</ul>
			{#each problems as p}<p class="section-foot warn">{p}</p>{/each}
			{#each warnings as w}<p class="section-foot warn">{w}</p>{/each}
			{#if friendStops.length}<p class="section-foot">{t('editor.friendStops', { n: friendStops.length })}</p>{/if}
			{#if soloHint && !problems.length}<p class="section-foot">{t('editor.oneMore')} {t('editor.inviteLater')}</p>
			{:else if !problems.length && !warnings.length}<p class="section-foot">{t('editor.orderHint')}</p>{/if}
		</div>

		<div class="section">
			<div class="section-head"><span class="caps">{t('editor.schedule')}</span></div>
			<div class="group">
				<button class="cell" bind:this={intervalBtn} onclick={() => (intervalOpen = true)} aria-haspopup="menu" aria-expanded={intervalOpen}>
					<span class="grow">{t('editor.runs')}</span>
					<span class="value menu-value">{intervalLabel(d.interval)} <Glyph name="updown" size={14} weight={2.2} /></span>
				</button>
				<div class="cell">
					<span class="grow">{t('fade.pause')}</span>
					<Switch bind:checked={d.paused} label={t('fade.pause')} />
				</div>
			</div>
			<p class="section-foot">{d.paused ? t('editor.pausedHint') : t('editor.scheduleHint')}</p>
		</div>

		<div class="section">
			<div class="section-head"><span class="caps">{t('editor.deleting')}</span></div>
			<div class="group">
				<div class="cell">
					<span class="grow">{t('editor.syncDeletes')}</span>
					<Switch bind:checked={d.deletions} label={t('editor.syncDeletes')} />
				</div>
				{#if d.deletions}
					<div class="cell">
						<span class="grow">{t('editor.askFrom')}</span>
						<span class="value">{t('n.songs', { n: d.guardMin })}</span>
						<span class="stepper">
							<button onclick={() => (d.guardMin = Math.max(0, d.guardMin - 1))} aria-label={t('common.less')} disabled={d.guardMin <= 0}>−</button>
							<span class="st-sep" aria-hidden="true"></span>
							<button onclick={() => (d.guardMin = Math.min(1000, d.guardMin + 1))} aria-label={t('common.more')}>+</button>
						</span>
					</div>
					<div class="cell">
						<span class="grow">{t('editor.orPct')}</span>
						<span class="value">{d.guardPct} %</span>
						<span class="stepper">
							<button onclick={() => (d.guardPct = Math.max(0, d.guardPct - 5))} aria-label={t('common.less')} disabled={d.guardPct <= 0}>−</button>
							<span class="st-sep" aria-hidden="true"></span>
							<button onclick={() => (d.guardPct = Math.min(100, d.guardPct + 5))} aria-label={t('common.more')} disabled={d.guardPct >= 100}>+</button>
						</span>
					</div>
				{/if}
			</div>
			<p class="section-foot">{d.deletions ? t('editor.deletesOn', { n: d.guardMin, pct: d.guardPct }) : t('editor.deletesOff')}</p>
		</div>

		<div class="section">
			<div class="section-head"><span class="caps">{t('editor.versions')}</span></div>
			<div class="group pad">
				<Segmented
					bind:value={d.versionPolicy}
					label={t('editor.versions')}
					options={[
						{ value: 'closest', label: t('editor.closest') },
						{ value: 'exact', label: t('editor.exact') }
					]}
				/>
			</div>
			<p class="section-foot">{d.versionPolicy === 'exact' ? t('editor.exactHint') : t('editor.closestHint')}</p>
		</div>

		{#if fade}
			<div class="section">
				<div class="group">
					<button class="cell center" onclick={askDelete}><span class="grow danger">{t('fade.delete')}</span></button>
				</div>
			</div>
		{/if}
	</div>
</Sheet>

<Menu
	bind:open={intervalOpen}
	anchor={intervalBtn}
	label={t('editor.runs')}
	items={intervals().map((it) => ({ label: it.label, checked: it.value === d.interval, run: () => (d.interval = it.value) }))}
/>

<Sheet bind:open={pickOpen} label={t('editor.addCollection')} class="tinted" style={tintVars(d.color)}>
	{#snippet header()}
		<SheetBar
			title={pickSvc ? SERVICE_NAME[pickSvc] : t('editor.addCollection')}
			leading={pickSvc && pickFor === null
				? { label: t('editor.backToServices'), icon: 'chevronLeft', run: () => paneSpring.to(0, { response: 0.38 }).then(() => (pickSvc = null)) }
				: { label: t('common.cancel'), run: () => (pickOpen = false) }}
		/>
	{/snippet}
	<div class="panes" style:--pane={pane}>
		<div class="pane" inert={!!pickSvc}>
			<div class="section" style="margin-top: 12px">
				<div class="group">
					{#each app.services as s (s.service)}
						{@const ok = s.connected && s.status !== 'error'}
						<button class="cell" style:--inset="58px" onclick={() => (ok ? chooseSvc(s.service) : ((pickOpen = false), ui.openAccount(s.service)))}>
							<ServiceMark service={s.service} size={30} />
							<span class="grow">
								<span class="clip b">{s.name}</span>
								<span class="t-foot clip b" class:t-2={ok} class:danger={s.connected && !ok}>
									{ok ? t('services.as', { account: s.display_name ?? '' }) : s.connected ? t('services.expiredShort') : t('services.notConnected')}
								</span>
							</span>
							<span class="chev"><Glyph name="chevronRight" size={18} weight={2.4} /></span>
						</button>
					{/each}
				</div>
				<p class="section-foot">{t('editor.moreServices')}</p>
			</div>
		</div>
		<div class="pane" inert={!pickSvc}>
			{#if pickSvc}
				<div class="section" style="margin-top: 12px">
					<label class="field filter">
						<Glyph name="search" size={16} weight={2.2} />
						<input bind:value={colFilter} placeholder={t('editor.filter')} autocomplete="off" />
						<button class="refresh" onclick={() => pickSvc && loadCollections(pickSvc, true)} aria-label={t('editor.reload')} disabled={colLoading}><Glyph name="refresh" size={16} weight={2.2} /></button>
					</label>
					{#if colError}<p class="section-foot warn">{colError}</p>{/if}
					<div class="group">
						{#if colLoading && !collections[pickSvc]}
							<div class="cell"><span class="grow t-2">{t('editor.loadingCollections')}</span></div>
						{/if}
						{#each shownCollections as c (c.kind + c.id)}
							{@const isTaken = taken(pickSvc, c)}
							<button class="cell" style:--inset="58px" disabled={isTaken} onclick={() => choose(c)}>
								<span class="tile-icon" style:background={c.kind === 'liked' ? '#ff2d55' : c.kind === 'library' ? '#8e8e93' : 'var(--tint-fill)'}>
									<Glyph name={c.kind === 'liked' ? 'heart' : c.kind === 'library' ? 'library' : 'note'} size={17} weight={2.2} />
								</span>
								<span class="grow">
									<span class="clip b">{c.name}</span>
									<span class="t-foot t-2 b">{kindLabel(c.kind)}{c.count != null ? ` · ${t('n.songs', { n: c.count })}` : ''}{!c.editable ? ` · ${t('editor.readOnly')}` : ''}</span>
								</span>
								{#if isTaken}<span class="t-foot t-2">{t('editor.already')}</span>{/if}
							</button>
						{/each}
						<button class="cell" style:--inset="58px" onclick={() => choose({ kind: 'playlist', id: '', name: newName }, true)}>
							<span class="add-ic"><Glyph name="plus" size={18} weight={2.6} /></span>
							<span class="grow">{t('editor.newPlaylistNamed', { name: newName })}</span>
						</button>
					</div>
					<p class="section-foot">{t('editor.newPlaylistHint')}</p>
				</div>
			{/if}
		</div>
	</div>
</Sheet>

<!-- what a receiving collection takes: everything, what fits a rule, or what no other rule takes -->
<Sheet bind:open={ruleOpen} label={t('rule.title')} class="tinted" style={tintVars(d.color)}>
	{#snippet header()}
		<SheetBar title={ruleStop?.collection_name || t('rule.title')} leading={{ label: t('common.cancel'), run: () => (ruleOpen = false) }} confirm={{ label: t('common.done'), run: saveRule, disabled: ruleEmpty }} />
	{/snippet}
	<div class="form">
		<div class="section">
			<div class="section-head"><span class="caps">{t('rule.takes')}</span></div>
			<div class="pad">
				<Segmented
					bind:value={ruleMode}
					label={t('rule.takes')}
					options={[
						{ value: 'all', label: t('rule.modeAll') },
						{ value: 'rule', label: t('rule.modeRule') },
						{ value: 'rest', label: t('rule.modeRest') }
					]}
				/>
			</div>
			<p class="section-foot">{ruleMode === 'all' ? t('rule.allHint') : ruleMode === 'rest' ? t('rule.restHint') : t('rule.ruleHint')}</p>
		</div>
		{#if ruleMode === 'rule'}
			<div class="section">
				<div class="group">
					<label class="cell"><span class="rlbl">{t('mcp.moods')}</span><input class="in" bind:value={rf.moods} placeholder={t('mcp.moodPlaceholder')} autocomplete="off" /></label>
					<label class="cell"><span class="rlbl">{t('mcp.genres')}</span><input class="in" bind:value={rf.genres} placeholder={t('rule.genrePh')} autocomplete="off" /></label>
					<label class="cell"><span class="rlbl">{t('mcp.tags')}</span><input class="in" bind:value={rf.tags} autocomplete="off" /></label>
					<label class="cell"><span class="rlbl">{t('rule.artists')}</span><input class="in" bind:value={rf.artists} placeholder={t('rule.artistsPh')} autocomplete="off" /></label>
					<label class="cell"><span class="rlbl">{t('rule.languages')}</span><input class="in" bind:value={rf.languages} placeholder={t('rule.languagesPh')} autocomplete="off" /></label>
					<div class="cell">
						<span class="rlbl">{t('rule.energy')}</span>
						<span class="chips">
							{#each ENERGY as e (e.value)}
								{@const on = energy.includes(e.value)}
								<button class="cell-pill chip" class:on aria-pressed={on} onclick={() => (energy = on ? energy.filter((x) => x !== e.value) : [...energy, e.value])}>{t(e.key)}</button>
							{/each}
						</span>
					</div>
				</div>
				<p class="section-foot" class:warn={ruleEmpty}>{ruleEmpty ? t('rule.needOne') : t('mcp.commas')}</p>
			</div>
			{#if vocab && (vocab.moods.length || vocab.genres.length || vocab.tags.length)}
				{#each [['moods', 'mcp.moods'], ['genres', 'mcp.genres'], ['tags', 'mcp.tags']] as const as [key, head] (key)}
					{#if vocab[key].length}
						<div class="section">
							<div class="section-head"><span class="caps">{t('rule.yours', { what: t(head) })}</span></div>
							<div class="chips wrap">
								{#each vocab[key].slice(0, 30) as l (l.label)}
									{@const on = hasLabel(key, l.label)}
									<button class="cell-pill chip" class:on aria-pressed={on} onclick={() => toggleLabel(key, l.label)}>{l.label} <span class="n">{l.songs}</span></button>
								{/each}
							</div>
						</div>
					{/if}
				{/each}
			{/if}
			<p class="section-foot spaced">{t('rule.labelsHint')}</p>
		{/if}
	</div>
</Sheet>

<ActionSheet bind:open={confirmDiscard} title={t('editor.discardQ')} actions={[{ label: t('editor.discard'), role: 'destructive', run: discard }]} />

<style>
	.form {
		padding: 4px 0 calc(var(--safe-bottom) + 48px);
	}
	.restored {
		margin: 0 16px 16px;
		padding: 8px 14px;
		border-radius: 18px;
		background: var(--fill-3);
		color: var(--label-2);
	}
	.restored .text-btn {
		min-height: 0;
		padding: 0;
	}
	.b {
		display: block;
	}

	.preview {
		display: flex;
		align-items: center;
		gap: 16px;
		margin: 6px 20px 22px;
	}
	.pv-cover {
		position: relative;
		flex: none;
	}
	.pv-badge {
		position: absolute;
		right: -8px;
		bottom: -8px;
		padding: 2px;
		border-radius: 12px;
		background: var(--sheet-solid);
	}
	.pv-text {
		min-width: 0;
	}
	.pv-name {
		font-size: var(--f-t3);
		font-weight: 700;
		letter-spacing: var(--tr-t3);
	}
	.pv-route {
		display: flex;
		align-items: center;
		gap: 4px;
		margin-top: 6px;
		min-height: 18px;
	}
	.pv-arr {
		color: var(--tint);
	}

	.lbl {
		width: 4.5em;
		flex: none;
	}
	.tinput {
		border: 0;
		outline: none;
		background: none;
		color: var(--label);
		font: inherit;
		min-width: 0;
		padding: 2px 0;
	}
	.warn {
		color: var(--orange-text);
	}

	.swatches,
	.icons {
		display: grid;
		grid-template-columns: repeat(8, minmax(0, 1fr));
		justify-items: center;
		gap: 6px;
		padding: 14px 16px;
	}
	.icons {
		row-gap: 8px;
		padding-top: 4px;
		border-top: 0.5px solid var(--sep);
		margin: 0 16px;
		padding-left: 0;
		padding-right: 0;
	}
	.sw {
		width: min(32px, 100%);
		aspect-ratio: 1;
		border-radius: 50%;
		background: var(--c);
		box-shadow:
			0 0 0 0 var(--cell-grouped),
			0 0 0 0 var(--c);
		transition: box-shadow 260ms cubic-bezier(0.2, 0.9, 0.3, 1.2);
	}
	.sw[aria-checked='true'] {
		box-shadow:
			0 0 0 3px var(--cell-grouped),
			0 0 0 5.5px var(--c);
	}
	.sw:active,
	.ic:active {
		transform: scale(0.9);
	}
	.ic {
		display: grid;
		place-items: center;
		width: min(38px, 100%);
		aspect-ratio: 1;
		margin-top: 8px;
		border-radius: 12px;
		color: var(--label-2);
		transition: transform 260ms cubic-bezier(0.25, 1.5, 0.45, 1);
	}

	.stops {
		overflow: visible;
	}
	.stops > li {
		position: relative;
		will-change: transform;
	}
	.stops > li:first-child {
		border-radius: var(--r-group) var(--r-group) 0 0;
		overflow: hidden;
	}
	.stops > li:last-child {
		border-radius: 0 0 var(--r-group) var(--r-group);
		overflow: hidden;
	}
	.lifted {
		z-index: 3;
		border-radius: 20px !important;
		overflow: hidden;
		box-shadow: 0 12px 30px -8px rgb(0 0 0 / 0.3);
	}
	.appear {
		animation: appear 420ms cubic-bezier(0.2, 0.9, 0.3, 1);
	}
	@keyframes appear {
		from {
			opacity: 0;
			transform: scale(0.96);
		}
	}
	/* icon | name + grip on one line, the direction control below spans the whole row */
	.stop {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		grid-template-areas: 'mark name grip' 'mark role role' 'mark follow follow' 'mark rule rule' 'mark move move';
		align-items: start;
		column-gap: 12px;
		padding-top: 12px;
		padding-bottom: 12px;
	}
	.stop > :global(:first-child) {
		grid-area: mark;
	}
	.stop .grow {
		display: contents;
	}
	.nm {
		grid-area: name;
		min-width: 0;
	}
	.role {
		grid-area: role;
		margin-top: 8px;
		max-width: 360px;
	}
	.follow {
		grid-area: follow;
		display: flex;
		align-items: center;
		gap: 12px;
		margin-top: 10px;
		max-width: 360px;
	}
	.follow.move {
		grid-area: move;
	}
	/* the rule of a receiving collection: one quiet line that opens its editor */
	.rule {
		grid-area: rule;
		justify-self: start;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		max-width: 100%;
		margin-top: 10px;
		padding: 5px 10px;
		border-radius: 999px;
		background: var(--fill-3);
		color: var(--label-2);
		font-size: var(--f-foot);
		font-weight: 600;
	}
	.rule.set {
		background: color-mix(in srgb, var(--tint-fill) 16%, transparent);
		color: var(--tint);
	}
	.follow .grow-text {
		flex: 1;
		min-width: 0;
	}
	.follow .fl {
		display: block;
	}
	.grip {
		grid-area: grip;
		align-self: start;
		margin-top: -6px;
		display: grid;
		place-items: center;
		width: 40px;
		height: 44px;
		margin-bottom: -14px; /* a full touch target without pushing the direction control down */
		margin-right: -10px;
		color: var(--label-3);
		touch-action: none;
		cursor: grab;
	}
	.grip:active {
		cursor: grabbing;
		color: var(--label-2);
	}
	.add {
		color: var(--tint);
	}
	.add-ic {
		display: grid;
		place-items: center;
		width: 30px;
		height: 30px;
		border-radius: 50%;
		background: var(--tint-fill);
		color: var(--on-tint);
	}
	.menu-value {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		color: var(--tint);
	}
	.stepper {
		display: inline-flex;
		align-items: center;
		height: 32px;
		margin-left: 8px;
		border-radius: 999px;
		background: var(--ctl);
		box-shadow: var(--ctl-shadow);
	}
	.stepper button {
		width: 46px;
		height: 32px;
		border-radius: 999px;
		font-size: 1.3rem;
		line-height: 1;
	}
	.stepper button:active {
		background: var(--fill-2);
	}
	.stepper button:disabled {
		color: var(--label-3);
	}
	.st-sep {
		width: 0.5px;
		height: 18px;
		background: var(--sep);
	}
	.center .grow {
		text-align: center;
	}
	.danger {
		color: var(--red-text);
	}
	:global(.proto) .cell:disabled {
		opacity: 0.45;
	}

	.choose {
		min-height: 0;
		padding: 0;
		font-weight: 600;
	}
	.pad {
		padding: 10px;
	}
	.filter {
		margin-bottom: 10px;
		padding-right: 6px;
	}
	.refresh {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		border-radius: 50%;
	}
	.panes {
		display: grid;
		grid-template-columns: 100% 100%;
		transform: translateX(calc(var(--pane) * -100%));
	}
	.pane {
		min-width: 0;
	}

	.rlbl {
		flex: none;
		width: 6.5em;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.chips.wrap {
		gap: 8px;
		padding: 0 4px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: var(--label);
		font-weight: 500;
	}
	.chip .n {
		color: var(--label-2);
		font-size: var(--f-foot);
	}
	.chip.on {
		background: var(--tint-fill);
		color: var(--on-tint);
	}
	.chip.on .n {
		color: inherit;
		opacity: 0.75;
	}
	.spaced {
		margin: 0 16px;
	}

	@media (prefers-reduced-motion: reduce) {
		.appear {
			animation: none;
		}
		.sw,
		.ic {
			transition: none;
		}
	}
</style>
