// Motion primitives: springs, velocity, momentum, rubber-banding.
// Springs are described the way Apple does it: damping ratio (1 = no overshoot) and response
// (seconds, how quickly the value gets there). They always continue from the current on-screen
// value and velocity, so any animation can be grabbed and redirected mid-flight.

export interface SpringConfig {
	/** 1 = critically damped (default). < 1 overshoots; use ~0.8 only after a flick. */
	damping?: number;
	/** seconds; lower = snappier. Not a duration: settle time emerges from the physics. */
	response?: number;
	/** initial velocity in units per second (px/s for positions). Omit to keep the current one. */
	velocity?: number;
}

export function reducedMotion(): boolean {
	return typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export class Spring {
	value: number;
	velocity = 0;
	target: number;
	#raf = 0;
	#last = 0;
	#k = 0;
	#c = 0;
	#resolve: ((done: boolean) => void) | null = null;
	#onChange: (v: number) => void;
	#precision: number;

	/** precision: settle threshold in the value's own unit (0.5 for px, 0.001 for 0…1 progress) */
	constructor(value: number, onChange: (v: number) => void, precision = 0.5) {
		this.value = value;
		this.target = value;
		this.#onChange = onChange;
		this.#precision = precision;
	}

	get moving() {
		return this.#raf !== 0;
	}

	/** Jump without animation (used while a finger is driving the value). */
	set(v: number) {
		this.stop();
		this.value = v;
		this.target = v;
		this.velocity = 0;
		this.#onChange(v);
	}

	/** Animate to target. Resolves true when settled, false when interrupted. */
	to(target: number, { damping = 1, response = 0.4, velocity }: SpringConfig = {}): Promise<boolean> {
		if (reducedMotion()) {
			this.set(target);
			return Promise.resolve(true);
		}
		this.target = target;
		if (velocity !== undefined) this.velocity = velocity;
		const w = (2 * Math.PI) / Math.max(response, 0.02);
		this.#k = w * w;
		this.#c = 2 * damping * w;
		const prev = this.#resolve;
		this.#resolve = null;
		prev?.(false);
		return new Promise((res) => {
			this.#resolve = res;
			if (!this.#raf) {
				this.#last = performance.now();
				this.#raf = requestAnimationFrame(this.#tick);
			}
		});
	}

	stop() {
		if (this.#raf) cancelAnimationFrame(this.#raf);
		this.#raf = 0;
		const r = this.#resolve;
		this.#resolve = null;
		r?.(false);
	}

	#tick = (now: number) => {
		let dt = Math.min((now - this.#last) / 1000, 1 / 20);
		this.#last = now;
		// fixed sub-steps keep the integration stable at any frame rate
		while (dt > 0) {
			const h = Math.min(1 / 240, dt);
			const a = -this.#k * (this.value - this.target) - this.#c * this.velocity;
			this.velocity += a * h;
			this.value += this.velocity * h;
			dt -= h;
		}
		const p = this.#precision;
		if (Math.abs(this.value - this.target) < p && Math.abs(this.velocity) < p * 10) {
			this.value = this.target;
			this.velocity = 0;
			this.#raf = 0;
			this.#onChange(this.value);
			const r = this.#resolve;
			this.#resolve = null;
			r?.(true);
			return;
		}
		this.#onChange(this.value);
		this.#raf = requestAnimationFrame(this.#tick);
	};
}

/** Where a flick would come to rest (Apple's scroll-deceleration projection). */
export function project(velocity: number, decelerationRate = 0.998): number {
	return ((velocity / 1000) * decelerationRate) / (1 - decelerationRate);
}

/** Progressive resistance past a boundary: the further you pull, the less it follows. */
export function rubberband(overshoot: number, dimension: number, constant = 0.55): number {
	return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}

/** Release velocity from the last ~100 ms of pointer samples (px/s). */
export class VelocityTracker {
	#s: { t: number; x: number; y: number }[] = [];
	reset() {
		this.#s = [];
	}
	add(x: number, y: number) {
		const t = performance.now();
		this.#s.push({ t, x, y });
		while (this.#s.length > 2 && t - this.#s[0].t > 100) this.#s.shift();
	}
	get(): { x: number; y: number } {
		const s = this.#s;
		if (s.length < 2) return { x: 0, y: 0 };
		const a = s[0];
		const b = s[s.length - 1];
		// finger rested before lifting: no momentum
		if (performance.now() - b.t > 80) return { x: 0, y: 0 };
		const dt = (b.t - a.t) / 1000;
		if (dt <= 0) return { x: 0, y: 0 };
		return { x: (b.x - a.x) / dt, y: (b.y - a.y) / dt };
	}
}

// One gesture at a time: the first recognizer that locks a direction owns the pointer,
// nested recognizers (swipe rows inside a pannable page) back off.
let owner: symbol | null = null;
export const gesture = {
	claim(token: symbol) {
		if (owner && owner !== token) return false;
		owner = token;
		return true;
	},
	owns: (token: symbol) => owner === token,
	busy: () => owner !== null,
	release(token: symbol) {
		if (owner === token) owner = null;
	}
};

/** Tiny haptic tick where supported (Android); harmless elsewhere. */
export function tick10() {
	try {
		navigator.vibrate?.(8);
	} catch {
		/* not supported */
	}
}
