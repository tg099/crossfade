// Drag to reorder a vertical list. The lifted row follows the pointer 1:1, the others make room
// with springs, and the drop settles into its slot before the order is committed.
// Keyboard: ↑/↓ on the handle moves one step.
import { tick } from 'svelte';
import { Spring, tick10 } from './motion.ts';

type Id = string | number;

export class Reorder {
	dragId = $state<Id | null>(null);
	dragY = $state(0);
	offsets = $state<Record<string, number>>({});
	#springs = new Map<Id, Spring>();
	#lift = new Spring(0, (v) => (this.dragY = v), 0.5);
	#drag: { from: number; to: number; y0: number; id: number; row: number; started: boolean } | null = null;
	#suppressClick = false;

	/**
	 * ids: current order. commit: apply a move. threshold: px before a press becomes a drag
	 * (0 for dedicated handles; >0 when the whole row is also clickable).
	 */
	ids: () => Id[];
	commit: (from: number, to: number) => void;
	threshold: number;
	constructor(ids: () => Id[], commit: (from: number, to: number) => void, threshold = 0) {
		// (Svelte's TS support has no parameter properties)
		this.ids = ids;
		this.commit = commit;
		this.threshold = threshold;
	}

	style(id: Id) {
		if (id === this.dragId) return `translate3d(0,${this.dragY}px,0) scale(1.02)`;
		const o = this.offsets[String(id)];
		return o ? `translate3d(0,${o}px,0)` : undefined;
	}

	#spring(id: Id) {
		let s = this.#springs.get(id);
		if (!s) {
			s = new Spring(0, (v) => (this.offsets[String(id)] = v), 0.5);
			this.#springs.set(id, s);
		}
		return s;
	}

	down = (e: PointerEvent, index: number) => {
		if (e.button !== 0) return;
		e.stopPropagation();
		const row = (e.currentTarget as HTMLElement).closest('li') ?? (e.currentTarget as HTMLElement);
		this.#drag = { from: index, to: index, y0: e.clientY, id: e.pointerId, row: row.offsetHeight, started: false };
		if (!this.threshold) this.#start(e);
	};

	#start(e: PointerEvent) {
		const d = this.#drag!;
		d.started = true;
		(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
		this.#lift.set(0);
		this.dragId = this.ids()[d.from];
		tick10();
	}

	move = (e: PointerEvent) => {
		const d = this.#drag;
		if (!d || e.pointerId !== d.id) return;
		if (!d.started) {
			if (Math.abs(e.clientY - d.y0) < this.threshold) return;
			this.#start(e);
		}
		const n = this.ids().length;
		const raw = e.clientY - d.y0;
		this.#lift.set(Math.max(-d.from * d.row - 16, Math.min((n - 1 - d.from) * d.row + 16, raw)));
		const to = Math.max(0, Math.min(n - 1, d.from + Math.round(this.dragY / d.row)));
		if (to === d.to) return;
		d.to = to;
		this.ids().forEach((id, k) => {
			if (k === d.from) return;
			const want = d.from < k && k <= to ? -d.row : to <= k && k < d.from ? d.row : 0;
			const s = this.#spring(id);
			if (s.target !== want) s.to(want, { response: 0.3 });
		});
	};

	up = async () => {
		const d = this.#drag;
		this.#drag = null;
		if (!d?.started) return;
		this.#suppressClick = true;
		await this.#lift.to((d.to - d.from) * d.row, { response: 0.3 });
		if (d.to !== d.from) this.commit(d.from, d.to);
		await tick();
		for (const s of this.#springs.values()) s.set(0);
		this.#lift.set(0);
		this.dragId = null;
	};

	/** call from the row's click handler (capture): a drag must not also open the row */
	click = (e: MouseEvent) => {
		if (!this.#suppressClick) return;
		this.#suppressClick = false;
		e.preventDefault();
		e.stopPropagation();
	};

	key = (e: KeyboardEvent, index: number, focusSel?: (id: Id) => string) => {
		const dir = e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' ? 1 : 0;
		const to = index + dir;
		if (!dir || to < 0 || to >= this.ids().length) return;
		e.preventDefault();
		const id = this.ids()[index];
		this.commit(index, to);
		if (focusSel) tick().then(() => document.querySelector<HTMLElement>(focusSel(id))?.focus());
	};
}
