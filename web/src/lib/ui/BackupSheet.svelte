<script lang="ts">
	import { ApiError, api, del, post } from '#lib/api.ts';
	import Glyph from './Glyph.svelte';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { i18n, num, t, type Key } from './i18n.svelte.ts';
	import { ago, ui } from './ui.svelte.ts';

	// Admin only: the database backups on the server, making one, downloading, restoring (also from a file).
	type Kind = 'start' | 'daily' | 'manual' | 'restore';
	type Backup = { name: string; at: number; schema: number; kind: Kind; bytes: number };
	type Overview = {
		backups: Backup[];
		daily: boolean;
		on_start: boolean;
		key_from_env: boolean;
		keep: Record<Kind, number>;
		schema: number;
	};
	type Inspect = { schema: number; users: number; fades: number; logins: number };

	let data = $state<Overview | null>(null);
	let open = $state<string | null>(null);
	let busy = $state<'' | 'make' | 'check' | 'restore'>('');
	let file: HTMLInputElement | undefined = $state();

	async function load() {
		data = await api<Overview>('/api/admin/backups');
	}
	$effect(() => {
		if (ui.backupOpen) load().catch(ui.fail);
		else open = null;
	});

	const bytes = (n: number) => {
		const u = ['B', 'KB', 'MB', 'GB'];
		let i = 0;
		while (n >= 1024 && i < u.length - 1) (n /= 1024), i++;
		return `${n.toLocaleString(i18n.locale, { maximumFractionDigits: n < 10 && i ? 1 : 0 })} ${u[i]}`;
	};
	const when = (ts: number) =>
		new Date(ts * 1000).toLocaleString(i18n.locale, { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });

	async function make() {
		busy = 'make';
		try {
			data = await post<Overview>('/api/admin/backups');
			ui.say(t('backup.made'));
		} catch (e) {
			ui.fail(e);
		} finally {
			busy = '';
		}
	}

	function restarted() {
		ui.say(t('backup.restored'));
		setTimeout(() => location.reload(), 900);
	}

	function restore(b: Backup) {
		ui.ask = {
			title: t('backup.restoreQ'),
			message: t('backup.restoreText', { date: when(b.at) }),
			label: t('backup.restore'),
			run: async () => {
				busy = 'restore';
				try {
					await post(`/api/admin/backups/${encodeURIComponent(b.name)}/restore`);
					restarted();
				} catch (e) {
					ui.fail(e);
					busy = '';
				}
			}
		};
	}

	function remove(b: Backup) {
		ui.ask = {
			title: t('backup.deleteQ'),
			message: when(b.at),
			label: t('backup.delete'),
			run: async () => {
				try {
					data = await del<Overview>(`/api/admin/backups/${encodeURIComponent(b.name)}`);
					open = null;
					ui.say(t('backup.deleted'), 'trash');
				} catch (e) {
					ui.fail(e);
				}
			}
		};
	}

	/** the raw file as the request body: no form encoding needed on the server */
	async function send(path: string, f: File): Promise<any> {
		const res = await fetch(path, { method: 'POST', headers: { 'X-Crossfade': '1', 'Content-Type': 'application/octet-stream' }, body: f, credentials: 'same-origin' });
		const body = await res.json().catch(() => null);
		if (!res.ok) throw new ApiError(typeof body?.detail === 'string' ? body.detail : t('err.status', { status: res.status }), res.status);
		return body;
	}

	async function picked() {
		const f = file?.files?.[0];
		if (file) file.value = '';
		if (!f) return;
		busy = 'check';
		let info: Inspect;
		try {
			info = await send('/api/admin/backup-upload/check', f);
		} catch (e) {
			ui.fail(e);
			return;
		} finally {
			busy = '';
		}
		ui.ask = {
			title: t('backup.uploadQ'),
			message: t('backup.uploadText', { users: info.users, fades: info.fades, logins: info.logins }),
			label: t('backup.restore'),
			run: async () => {
				busy = 'restore';
				try {
					await send('/api/admin/backup-upload/restore', f);
					restarted();
				} catch (e) {
					ui.fail(e);
					busy = '';
				}
			}
		};
	}
</script>

<Sheet bind:open={ui.backupOpen} label={t('backup.title')}>
	{#snippet header()}
		<SheetBar title={t('backup.title')} leading={{ label: t('common.back'), icon: 'chevronLeft', run: () => (ui.backupOpen = false) }} />
	{/snippet}
	<div class="wrap">
		<p class="intro t-sub t-2">{t('backup.intro')}</p>

		<div class="section">
			<div class="group">
				<button class="cell" style:--inset="58px" onclick={make} disabled={!!busy}>
					<span class="tile-icon" style:background="#0a84ff"><Glyph name="archive" size={18} weight={2.2} /></span>
					<span class="grow tintc">{busy === 'make' ? t('backup.making') : t('backup.now')}</span>
				</button>
				<button class="cell" style:--inset="58px" onclick={() => file?.click()} disabled={!!busy}>
					<span class="tile-icon" style:background="#8e8e93"><Glyph name="undo" size={18} weight={2.2} /></span>
					<span class="grow">{busy === 'check' ? t('backup.checking') : busy === 'restore' ? t('backup.restoring') : t('backup.upload')}</span>
				</button>
			</div>
			<p class="section-foot">{t('backup.uploadHint')}</p>
			<input bind:this={file} type="file" accept=".sqlite3,.sqlite,.db,application/vnd.sqlite3,application/x-sqlite3" hidden onchange={picked} />
		</div>

		{#if data}
			<div class="section">
				<div class="section-head"><span class="caps">{t('backup.saved')}</span><span class="t-foot t-2">{num(data.backups.length)}</span></div>
				{#if data.backups.length}
					<ul class="group">
						{#each data.backups as b (b.name)}
							<li>
								<button class="cell" aria-expanded={open === b.name} onclick={() => (open = open === b.name ? null : b.name)}>
									<span class="grow">
										<span class="b">{when(b.at)}</span>
										<span class="b t-foot t-2">{t(`backup.kind.${b.kind}` as Key)} · {bytes(b.bytes)}{b.schema !== data.schema ? ` · ${t('backup.schema', { n: b.schema })}` : ''}</span>
									</span>
									<span class="t-foot t-2">{ago(b.at)}</span>
									<span class="chev" class:turned={open === b.name}><Glyph name="chevronDown" size={16} weight={2.6} /></span>
								</button>
								{#if open === b.name}
									<div class="actions">
										<a class="pb glass lq pb-small" href={`/api/admin/backups/${encodeURIComponent(b.name)}`} download={b.name}><Glyph name="download" size={16} weight={2.4} />{t('backup.download')}</a>
										<button class="pb glass lq pb-small" onclick={() => restore(b)} disabled={!!busy}><Glyph name="undo" size={16} weight={2.4} />{t('backup.restore')}</button>
										<button class="pb glass lq pb-small danger" onclick={() => remove(b)} disabled={!!busy}><Glyph name="trash" size={16} weight={2.4} />{t('backup.delete')}</button>
									</div>
								{/if}
							</li>
						{/each}
					</ul>
				{:else}
					<p class="section-foot">{t('backup.none')}</p>
				{/if}
				<p class="section-foot">
					{t('backup.keep', { start: data.keep.start, daily: data.keep.daily, manual: data.keep.manual })}
					{#if !data.daily}{t('backup.dailyOff')}{/if}
				</p>
			</div>

			<div class="section">
				<div class="section-head"><span class="caps">{t('backup.keyHead')}</span></div>
				<div class="group key">
					<span class="tile-icon" style:background="#ff9f0a"><Glyph name="key" size={18} weight={2.2} /></span>
					<p class="t-foot">{t('backup.key', { where: data.key_from_env ? t('backup.keyEnv') : t('backup.keyFile') })}</p>
				</div>
			</div>
		{/if}
	</div>
</Sheet>

<style>
	.wrap {
		padding: 8px 0 calc(var(--safe-bottom) + 40px);
	}
	.intro {
		padding: 0 20px 22px;
	}
	.b {
		display: block;
	}
	.b:first-child {
		font-weight: 600;
	}
	.chev {
		color: var(--label-3);
		transition: transform 0.2s;
	}
	.chev.turned {
		transform: rotate(180deg);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		padding: 0 16px 14px;
	}
	.actions .pb {
		gap: 6px;
		text-decoration: none;
	}
	.key {
		display: flex;
		align-items: flex-start;
		gap: 12px;
		padding: 14px 16px;
	}
	.key p {
		margin: 0;
		color: var(--label-2);
	}
	@media (prefers-reduced-motion: reduce) {
		.chev {
			transition: none;
		}
	}
</style>
