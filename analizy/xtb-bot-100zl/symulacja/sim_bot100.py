#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Symulacja Monte Carlo: „bot zarabia 100 zł dziennie i wychodzi” (konto XTB).

Wariant A – bot ze stop lossem: transakcje sekwencyjne, każda ryzykuje R = r·kapitał,
  wygrana = +k·R, przegrana = −R, koszt c·R od KAŻDEJ transakcji. Dzień kończy się przy
  zysku ≥ +G, stracie ≤ −L albo po MAX_TRADES transakcjach. Ruina = kapitał < RUIN_FRAC·start.
Wariant C – martingale: jak A (p = p0, k = 1, c = 10 %, r = 1 %), bez limitu straty dziennej,
  po każdej stracie mnożnik ryzyka ×2 aż dzień osiągnie +G (wtedy mnożnik wraca do 1);
  ryzyko nie przekracza MART_CAP kapitału. Mnożnik resetuje się na początku dnia.
Uruchomienie: python3 sim_bot100.py   (wyniki: results.json, tables_full.md, *.png w tym katalogu)
"""
import json, os, time, sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter

# ============================ PARAMETRY ============================
OUT = os.path.dirname(os.path.abspath(__file__))
N_PATHS   = 5000          # ścieżek Monte Carlo na kombinację
DAYS_YEAR = 250           # dni handlowych w roku
YEARS     = 5             # horyzont
N_DAYS    = DAYS_YEAR * YEARS
G         = 100.0         # cel dzienny [zł]
L         = 200.0         # dzienny limit straty [zł]
MAX_TRADES = 8            # maks. transakcji dziennie
CAPITALS  = [5000, 10000, 25000, 50000, 100000]
R_LIST    = [0.01, 0.02]  # ryzyko na transakcję jako ułamek kapitału
K_LIST    = [1.0, 1.5]    # zysk : ryzyko
C_LIST    = [0.10, 0.20, 0.05]  # koszt transakcyjny jako ułamek R (baza, 2×, 0,5×)
EDGES_PP  = [0, 2, 5, 10] # przewaga w punktach procentowych ponad p0 = 1/(1+k)
SWEEP_PP  = list(range(0, 31))  # do wykresu 3 (25 000 zł, r=1 %, k=1)
RUIN_FRAC = 0.5           # ruina = kapitał < 50 % startu
MART_CAP  = 0.30          # martingale: maks. ryzyko 30 % kapitału
SEED      = 20260923      # te same liczby losowe dla każdej kombinacji (wspólne liczby losowe)
WORKERS   = 4

# paleta (zwalidowana skillem dataviz): niebieski = referencja, zielony = zysk, czerwony = strata/ruina
BLUE, GREEN, RED, GRAY = "#2a78d6", "#008300", "#e34948", "#6b6a66"
INK, INK2 = "#0b0b0b", "#52514e"

# ============================ SYMULACJA ============================
def simulate(capitals, p, k, c, r, exit_on_target=True, loss_limit=True,
             martingale=False, n_days=N_DAYS, n_paths=N_PATHS, seed=SEED):
    """Zwraca słownik {dzień_snapshotu: {metryki per kapitał}}. Wektoryzacja: (kapitały × ścieżki)."""
    rng = np.random.default_rng(seed)
    cap0 = np.asarray(capitals, float)[:, None]
    C = np.repeat(cap0, n_paths, axis=1)
    alive = np.ones(C.shape, bool)
    peak = C.copy()
    maxdd = np.zeros(C.shape)
    target_days = np.zeros(C.shape, np.int64)
    active_days = np.zeros(C.shape, np.int64)
    m = np.ones(C.shape)
    snap_days = sorted(set([min(DAYS_YEAR, n_days), n_days]))
    snaps = {}
    for d in range(n_days):
        day_open = alive.copy()
        daypnl = np.zeros(C.shape)
        active_days += alive
        if martingale:
            m[:] = 1.0
        for t in range(MAX_TRADES):
            u = rng.random(C.shape)          # zawsze losujemy, żeby zachować wspólne liczby losowe
            win = u < p
            if martingale:
                R = np.minimum(m * r * C, MART_CAP * C)
            else:
                R = r * C
            delta = np.where(win, (k - c) * R, -(1.0 + c) * R)
            delta[~day_open] = 0.0
            C += delta
            daypnl += delta
            if martingale:
                m = np.where(day_open & ~win, m * 2.0, m)
            if exit_on_target:
                day_open &= ~(daypnl >= G)
            if loss_limit:
                day_open &= ~(daypnl <= -L)
            alive &= ~(C < RUIN_FRAC * cap0)
            day_open &= alive
        target_days += (active_days > 0) & (daypnl >= G)   # dzień zakończony wynikiem ≥ +G
        peak = np.maximum(peak, C)
        maxdd = np.maximum(maxdd, 1.0 - C / peak)
        if (d + 1) in snap_days:
            res = {}
            for i, cap in enumerate(capitals):
                pnl = C[i] - cap
                res[str(cap)] = {
                    "P_plus": float(np.mean(pnl > 0)),
                    "mediana": float(np.median(pnl)),
                    "p5": float(np.percentile(pnl, 5)),
                    "p95": float(np.percentile(pnl, 95)),
                    "srednia": float(np.mean(pnl)),
                    "P_dd30": float(np.mean(maxdd[i] > 0.30)),
                    "P_ruina": float(np.mean(~alive[i])),
                    "sredni_dzien": float(np.mean(pnl) / (d + 1)),
                    "pct_dni_cel": float(target_days[i].sum() / max(1, active_days[i].sum())),
                }
            snaps[str(d + 1)] = res
    return snaps

def run_spec(spec):
    t0 = time.time()
    kw = {k: v for k, v in spec.items() if k not in ("name",)}
    out = simulate(**kw)
    return spec["name"], out, time.time() - t0

def p0_of(k):
    return 1.0 / (1.0 + k)

# ============================ GŁÓWNY PRZEBIEG ============================
def main():
    t_all = time.time()
    specs = []
    # Wariant A – z regułą wyjścia po +100
    for r in R_LIST:
        for k in K_LIST:
            for c in C_LIST:
                for e in EDGES_PP:
                    specs.append(dict(name=f"A|r={r}|k={k}|c={c}|pp={e}|exit=1",
                                      capitals=CAPITALS, p=p0_of(k) + e / 100, k=k, c=c, r=r))
    # Wariant A – bez reguły wyjścia (zawsze do 8 transakcji), koszt bazowy
    for r in R_LIST:
        for k in K_LIST:
            for e in EDGES_PP:
                specs.append(dict(name=f"A|r={r}|k={k}|c=0.1|pp={e}|exit=0",
                                  capitals=CAPITALS, p=p0_of(k) + e / 100, k=k, c=0.10, r=r,
                                  exit_on_target=False))
    # Wariant C – martingale
    specs.append(dict(name="C|martingale", capitals=CAPITALS, p=0.5, k=1.0, c=0.10, r=0.01,
                      exit_on_target=True, loss_limit=False, martingale=True))
    # Test poprawności: p = p0, c = 0, bez reguły wyjścia → średni wynik dzienny ≈ 0
    specs.append(dict(name="SANITY|c=0|exit=0|limit=1", capitals=CAPITALS, p=0.5, k=1.0, c=0.0, r=0.01,
                      exit_on_target=False, loss_limit=True))
    specs.append(dict(name="SANITY|c=0|exit=0|limit=0", capitals=CAPITALS, p=0.5, k=1.0, c=0.0, r=0.01,
                      exit_on_target=False, loss_limit=False))
    # Przemiatanie przewagi (wykres 3 + wrażliwość na koszt), 25 000 zł, 1 rok
    for c in C_LIST:
        for e in SWEEP_PP:
            specs.append(dict(name=f"SWEEP|c={c}|pp={e}", capitals=[25000], p=0.5 + e / 100, k=1.0,
                              c=c, r=0.01, n_days=DAYS_YEAR))
    print(f"Uruchamiam {len(specs)} przebiegów × {N_PATHS} ścieżek ...", flush=True)
    results = {}
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        for name, out, dt in ex.map(run_spec, specs):
            results[name] = out
            print(f"  {name:45s} {dt:5.1f} s", flush=True)
    meta = dict(N_PATHS=N_PATHS, DAYS_YEAR=DAYS_YEAR, YEARS=YEARS, G=G, L=L, MAX_TRADES=MAX_TRADES,
                CAPITALS=CAPITALS, R_LIST=R_LIST, K_LIST=K_LIST, C_LIST=C_LIST, EDGES_PP=EDGES_PP,
                RUIN_FRAC=RUIN_FRAC, MART_CAP=MART_CAP, SEED=SEED, czas_s=round(time.time() - t_all, 1))
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "wyniki": results}, f, ensure_ascii=False, indent=1)
    write_tables(results)
    plots(results)
    print(f"Gotowe w {time.time() - t_all:.0f} s")

# ============================ TABELE ============================
def fmt_zl(x):
    return f"{x:,.0f}".replace(",", " ")

def row(res, cap, y):
    m = res[str(y)][str(cap)]
    return (f"| {fmt_zl(cap)} | {m['P_plus']*100:.0f} % | {fmt_zl(m['mediana'])} | {fmt_zl(m['p5'])} | "
            f"{m['P_dd30']*100:.0f} % | {m['P_ruina']*100:.1f} % | {m['sredni_dzien']:.0f} | {m['pct_dni_cel']*100:.0f} % |")

HDR = ("| Kapitał [zł] | P(na plusie) | Mediana [zł] | 5. percentyl [zł] | P(obsunięcie >30 %) | P(ruina) | Śr. wynik/dzień [zł] | Dni z celem +100 |\n"
       "|---:|---:|---:|---:|---:|---:|---:|---:|")

def write_tables(results):
    lines = ["# Wyniki symulacji – pełne tabele\n",
             f"Ścieżek: {N_PATHS}; rok = {DAYS_YEAR} dni; cel dzienny +{G:.0f} zł; limit straty −{L:.0f} zł; maks. {MAX_TRADES} transakcji/dzień; ruina = kapitał < {RUIN_FRAC*100:.0f} % startu.\n"]
    for r in R_LIST:
        for k in K_LIST:
            for c in C_LIST:
                for e in EDGES_PP:
                    name = f"A|r={r}|k={k}|c={c}|pp={e}|exit=1"
                    res = results[name]
                    for y, lab in ((DAYS_YEAR, "1 rok"), (N_DAYS, "5 lat")):
                        lines.append(f"\n## A: r = {r*100:.0f} %, k = {k}, koszt = {c*100:.0f} % R, przewaga +{e} pp (p = {(p0_of(k)+e/100)*100:.0f} %), horyzont {lab} (wynik = całość, śr./dzień = całość/{y})\n")
                        lines.append(HDR)
                        for cap in CAPITALS:
                            lines.append(row(res, cap, y))
    for r in R_LIST:
        for k in K_LIST:
            for e in EDGES_PP:
                name = f"A|r={r}|k={k}|c=0.1|pp={e}|exit=0"
                res = results[name]
                lines.append(f"\n## A BEZ reguły wyjścia po +100: r = {r*100:.0f} %, k = {k}, koszt 10 % R, przewaga +{e} pp, horyzont 1 rok\n")
                lines.append(HDR)
                for cap in CAPITALS:
                    lines.append(row(res, cap, DAYS_YEAR))
    res = results["C|martingale"]
    for y, lab in ((DAYS_YEAR, "1 rok"), (N_DAYS, "5 lat")):
        lines.append(f"\n## C: martingale (p = 50 %, k = 1, koszt 10 % R, r start 1 %, maks. 30 % kapitału), horyzont {lab}\n")
        lines.append(HDR)
        for cap in CAPITALS:
            lines.append(row(res, cap, y))
    for nm in ("SANITY|c=0|exit=0|limit=1", "SANITY|c=0|exit=0|limit=0"):
        res = results[nm]
        lines.append(f"\n## Test poprawności {nm} (p = 50 %, k = 1, koszt 0), horyzont 1 rok\n")
        lines.append(HDR)
        for cap in CAPITALS:
            lines.append(row(res, cap, DAYS_YEAR))
    lines.append("\n## Przemiatanie przewagi, 25 000 zł, r = 1 %, k = 1, 1 rok (średni wynik/dzień [zł])\n")
    lines.append("| Przewaga [pp] | p | koszt 5 % R | koszt 10 % R | koszt 20 % R |\n|---:|---:|---:|---:|---:|")
    for e in SWEEP_PP:
        vals = [results[f"SWEEP|c={c}|pp={e}"][str(DAYS_YEAR)]["25000"]["sredni_dzien"] for c in (0.05, 0.10, 0.20)]
        lines.append(f"| +{e} | {50+e} % | {vals[0]:.0f} | {vals[1]:.0f} | {vals[2]:.0f} |")
    with open(os.path.join(OUT, "tables_full.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

# ============================ WYKRESY ============================
def style_ax(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c9c8c2")
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color="#e6e5df", linewidth=0.8)
    ax.set_axisbelow(True)

def plots(results):
    plt.rcParams.update({"font.family": "DejaVu Sans", "figure.facecolor": "white", "axes.facecolor": "white",
                         "text.color": INK, "axes.labelcolor": INK2, "axes.titlecolor": INK})
    zl = FuncFormatter(lambda x, _: fmt_zl(x))
    # ---------- Wykres 1: histogramy rocznego wyniku, A, 25 000 zł ----------
    # Histogramy liczymy z osobnego, deterministycznego przebiegu (te same parametry), żeby mieć surowe ścieżki.
    fig, axes = plt.subplots(2, 2, figsize=(10, 6), dpi=150, sharex=True)
    bins = np.linspace(-15000, 40000, 56)
    for ax, e in zip(axes.ravel(), EDGES_PP):
        pnl = raw_year_pnl(25000, p=0.5 + e / 100, k=1.0, c=0.10, r=0.01)
        neg, pos = pnl[pnl < 0], pnl[pnl >= 0]
        ax.hist(neg, bins=bins, color=RED, edgecolor="white", linewidth=0.5)
        ax.hist(pos, bins=bins, color=GREEN, edgecolor="white", linewidth=0.5)
        ax.axvline(G * DAYS_YEAR, color=BLUE, linestyle="--", linewidth=1.5)
        ax.axvline(0, color=INK2, linewidth=0.8)
        pp = np.mean(pnl > 0) * 100
        ax.set_title(f"Przewaga +{e} pp (trafność {50+e} %)", fontsize=11, loc="left")
        ax.text(0.98, 0.95, f"na plusie: {pp:.0f} % ścieżek\nmediana: {fmt_zl(np.median(pnl))} zł",
                transform=ax.transAxes, ha="right", va="top", fontsize=9, color=INK2)
        style_ax(ax)
        ax.set_yticks([])
        ax.spines["left"].set_visible(False)
    axes[1, 0].set_xlabel("Wynik po roku [zł]"); axes[1, 1].set_xlabel("Wynik po roku [zł]")
    axes[0, 0].xaxis.set_major_formatter(zl)
    fig.suptitle("Wynik po roku: bot ze stop lossem, kapitał 25 000 zł, ryzyko 1 %, koszt 10 % ryzyka",
                 fontsize=13, x=0.02, ha="left")
    fig.text(0.02, 0.925, "czerwony = rok na stracie, zielony = rok na plusie, niebieska kreska = cel 100 zł × 250 dni = 25 000 zł",
             fontsize=9, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(os.path.join(OUT, "wykres1_histogram_roczny.png"))
    plt.close(fig)

    # ---------- Wykres 2: P(ruina w 5 lat) vs kapitał ----------
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    series = [("Bot ze stop lossem, ryzyko 1 %, przewaga +2 pp", results["A|r=0.01|k=1.0|c=0.1|pp=2|exit=1"], BLUE),
              ("Bot ze stop lossem, ryzyko 2 %, przewaga +2 pp", results["A|r=0.02|k=1.0|c=0.1|pp=2|exit=1"], GRAY),
              ("Martingale (podwajanie po stracie), przewaga 0 pp", results["C|martingale"], RED)]
    x = np.arange(len(CAPITALS)); w = 0.26
    for j, (lab, res, col) in enumerate(series):
        vals = [res[str(N_DAYS)][str(cap)]["P_ruina"] * 100 for cap in CAPITALS]
        bars = ax.bar(x + (j - 1) * w, vals, width=w - 0.03, color=col, label=lab)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.0f} %" if v >= 0.5 else "≈0 %", ha="center", fontsize=8, color=INK2)
    ax.set_xticks(x); ax.set_xticklabels([fmt_zl(c) + " zł" for c in CAPITALS])
    ax.set_ylim(0, 110); ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
    ax.set_ylabel("Prawdopodobieństwo ruiny w 5 lat (kapitał spada poniżej 50 % startu)")
    ax.set_xlabel("Kapitał startowy")
    ax.set_title("Ryzyko ruiny w 5 lat: stop loss kontra martingale", fontsize=13, loc="left")
    ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=1)
    style_ax(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "wykres2A_ruina_vs_kapital_stoploss_martingale.png"), bbox_inches="tight")
    plt.close(fig)

    # ---------- Wykres 3: średni wynik dzienny vs przewaga ----------
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    xs = np.array(SWEEP_PP, float)
    ys = np.array([results[f"SWEEP|c=0.1|pp={e}"][str(DAYS_YEAR)]["25000"]["sredni_dzien"] for e in SWEEP_PP])
    ax.plot(xs, ys, color=INK2, linewidth=2, zorder=2)
    ax.scatter(xs[ys < 0], ys[ys < 0], color=RED, s=45, zorder=3, label="średni wynik ujemny")
    ax.scatter(xs[ys >= 0], ys[ys >= 0], color=GREEN, s=45, zorder=3, label="średni wynik dodatni")
    ax.axhline(G, color=BLUE, linestyle="--", linewidth=1.5, label="cel: 100 zł/dzień")
    ax.axhline(0, color="#c9c8c2", linewidth=1)
    need = required_edge(xs, ys, G)
    be = required_edge(xs, ys, 0.0)
    if need is not None:
        ax.axvline(need, color=BLUE, linewidth=1, alpha=0.6)
        ax.annotate(f"cel 100 zł/dzień wymaga ok. +{need:.1f} pp\n(trafność ≈ {50+need:.0f} % przy stosunku zysk:ryzyko 1:1)",
                    xy=(need, G), xytext=(need - 14, G + 60), fontsize=9, color=INK,
                    arrowprops=dict(arrowstyle="-", color=BLUE, alpha=0.6))
    if be is not None:
        ax.annotate(f"próg opłacalności (koszty): +{be:.1f} pp", xy=(be, 0), xytext=(be + 1.0, -90),
                    fontsize=9, color=INK, arrowprops=dict(arrowstyle="-", color=INK2))
    ax.set_xlabel("Przewaga bota ponad losowość [punkty procentowe trafności]")
    ax.set_ylabel("Średni wynik dzienny w 1. roku [zł]")
    ax.set_title("Ile przewagi trzeba, żeby wyjść na 100 zł dziennie? (25 000 zł, ryzyko 1 %, koszt 10 % ryzyka)",
                 fontsize=12, loc="left")
    ax.set_xticks(xs[::2]); ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"+{v:.0f}"))
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    style_ax(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "wykres3_wynik_vs_przewaga.png"))
    plt.close(fig)

def required_edge(xs, ys, level):
    for i in range(1, len(xs)):
        if ys[i - 1] < level <= ys[i]:
            return float(xs[i - 1] + (level - ys[i - 1]) / (ys[i] - ys[i - 1]) * (xs[i] - xs[i - 1]))
    return None

def raw_year_pnl(cap, p, k, c, r):
    """Surowe roczne wyniki (do histogramu) – ta sama mechanika co simulate(), 1 rok, bez snapshotów."""
    rng = np.random.default_rng(SEED)
    C = np.full((1, N_PATHS), float(cap)); alive = np.ones(C.shape, bool)
    for d in range(DAYS_YEAR):
        day_open = alive.copy(); daypnl = np.zeros(C.shape)
        for t in range(MAX_TRADES):
            win = rng.random(C.shape) < p
            R = r * C
            delta = np.where(win, (k - c) * R, -(1 + c) * R); delta[~day_open] = 0.0
            C += delta; daypnl += delta
            day_open &= ~(daypnl >= G); day_open &= ~(daypnl <= -L)
            alive &= ~(C < RUIN_FRAC * cap); day_open &= alive
    return C[0] - cap

if __name__ == "__main__":
    main()
