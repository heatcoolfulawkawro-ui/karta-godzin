#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Symulacja Monte Carlo — wariant B: "Bez stop lossa — trzymam pozycję, aż będzie +100 zł" (walec parowy).

Model:
  * cena = geometryczny ruch Browna, 480 kroków 1-min na dzień handlowy, dryf 0 (w cenie),
  * bot otwiera pozycję na początku dnia (jeśli nie ma otwartej), kierunek losowy (p_win = 0,50 lub 0,52),
  * nominał N = m * aktualny kapitał (gotówka) w chwili otwarcia; depozyt = N/20 (dźwignia 20:1),
  * koszt: spread 0,02 % N przy otwarciu, swap 0,01 % N za każdą noc,
  * zamknięcie: wynik netto pozycji (PnL - spread - swapy) >= G = 100 zł  -> "cel",
                albo kapitał (gotówka + wynik netto pozycji) <= 50 % depozytu -> "stop-out" (strata realizowana),
  * ruina = kapitał < 50 % kapitału startowego (sprawdzane na koniec dnia i po stop-oucie); po ruinie ścieżka zamiera,
  * po zamknięciu pozycji bot kończy dzień; następnego dnia otwiera nową.
Wektoryzacja: pętla po dniach, w każdym dniu jedna macierz (ścieżki x 480) wspólna dla wszystkich
kombinacji (kapitał x mnożnik) danej pary (sigma, p_win); pierwsze przebicie progu z bieżącego max/min.
"""
import json, os, sys, time
import numpy as np

# ----------------------------------------------------------------------------- PARAMETRY
OUT_DIR       = os.path.dirname(os.path.abspath(__file__))
SEED          = 20260923
N_PATHS       = int(os.environ.get("N_PATHS", "3000"))    # liczba ścieżek Monte Carlo
STEPS_PER_DAY = 480                                       # kroki 1-min na dzień
DAYS_PER_YEAR = 250
YEARS         = 5                                         # horyzont główny (ruina 5 lat)
G             = 100.0                                     # cel dzienny [zł]
CAPITALS      = [5_000, 10_000, 25_000, 50_000, 100_000]  # kapitał startowy [zł]
MULTS         = [2, 5, 20]                                # mnożnik nominału m (N = m * kapitał)
LEVERAGE      = 20                                        # dźwignia regulacyjna -> depozyt = N/20
STOPOUT_LVL   = 0.50                                      # stop-out gdy kapitał <= 50 % depozytu
RUIN_LVL      = 0.50                                      # ruina gdy kapitał < 50 % startu
SPREAD        = 0.0002                                    # 0,02 % nominału przy otwarciu
SWAP          = 0.0001                                    # 0,01 % nominału za noc
SIGMAS        = {"0.5%": 0.005, "1%": 0.01, "3%": 0.03}   # zmienność dzienna
REF           = dict(K=25_000, m=5, sigma="1%")           # kombinacja referencyjna do wykresów 1 i 3
N_STORE_PATHS = 300
DEBUG = bool(os.environ.get('DEBUG'))
DEBUG_LOG = []                                       # ile ścieżek kapitału dzień po dniu zapisać dla wykresu 1

# Paleta (dataviz, zwalidowana): czerwony = strata/ruina, zielony = zysk, niebieski = referencja, szary = neutralny
C_RED, C_GREEN, C_BLUE, C_GRAY = "#e34948", "#008300", "#2a78d6", "#52514e"


# ----------------------------------------------------------------------------- SILNIK
def simulate(sigma_d, p_win, n_paths, n_days, capitals, mults, spread, swap,
             stopout=True, ruin_stop=True, seed=SEED, store_ref=None, verbose=True):
    """Zwraca słownik wyników per kombinacja (K, m) + opcjonalnie dzienny kapitał ścieżek referencyjnych."""
    rng = np.random.default_rng(seed)
    S = STEPS_PER_DAY
    sig_s = sigma_d / np.sqrt(S)
    drift_s = -0.5 * sig_s ** 2                        # GBM z zerowym dryfem ceny
    combos = [(K, m) for K in capitals for m in mults]
    C = len(combos)
    P = n_paths
    K0 = np.array([c[0] for c in combos], dtype=np.float64)[:, None]   # (C,1)
    M  = np.array([c[1] for c in combos], dtype=np.float64)[:, None]

    # stan
    cash   = np.repeat(K0, P, axis=1).astype(np.float64)   # gotówka (bez otwartej pozycji)
    is_open = np.zeros((C, P), bool)
    direc  = np.ones((C, P), np.int8)
    off    = np.zeros((C, P))                                # log-zwrot od wejścia do początku dnia
    costs  = np.zeros((C, P))                                # spread + swapy bieżącej pozycji
    Npos   = np.zeros((C, P))                                # nominał bieżącej pozycji
    margin = np.zeros((C, P))
    hold   = np.zeros((C, P), np.int32)                      # dni trzymania bieżącej pozycji
    ruined = np.zeros((C, P), bool)
    ruin_day = np.full((C, P), -1, np.int32)
    equity = cash.copy()

    # liczniki
    days_with_pos = np.zeros((C, P), np.int64)
    n_target = np.zeros((C, P), np.int64)
    n_stop   = np.zeros((C, P), np.int64)
    n_trades = np.zeros((C, P), np.int64)
    sum_target_hold = np.zeros((C, P), np.int64)
    hold_hist = np.zeros((C, n_days + 2), np.int64)         # histogram czasu trzymania do celu
    worst_trade = np.zeros((C, P))                           # najgorszy pojedynczy zrealizowany wynik
    worst_ratio = np.zeros((C, P))                           # najgorszy wynik / stan konta tuż przed stratą
    sum_realized = np.zeros((C, P))
    sum_target_pnl = np.zeros((C, P))
    stop_loss_total = np.zeros((C, P))
    swap_total = np.zeros((C, P))
    spread_total = np.zeros((C, P))
    equity_year = np.zeros((C, n_days // DAYS_PER_YEAR + 1, P))   # kapitał na koniec każdego roku
    ref_idx = combos.index((store_ref["K"], store_ref["m"])) if store_ref else None
    ref_daily = np.zeros((n_days + 1, min(N_STORE_PATHS, P))) if store_ref else None
    if store_ref:
        ref_daily[0] = cash[ref_idx, :N_STORE_PATHS]
    rows = np.arange(P)
    t0 = time.time()
    for t in range(n_days):
        Z = rng.standard_normal((P, S), dtype=np.float32)
        L = np.cumsum(drift_s + sig_s * Z, axis=1, dtype=np.float32)   # (P,S) log-zwrot od początku dnia
        cmax = np.maximum.accumulate(L, axis=1)
        cmin = np.minimum.accumulate(L, axis=1)
        L_end = L[:, -1].astype(np.float64)
        # kierunek dla nowo otwieranych pozycji: 52/48 = zgadnięcie znaku dziennego zwrotu z prawd. p_win
        guess_ok = rng.random(P) < p_win
        day_sign = np.where(L_end >= 0, 1, -1).astype(np.int8)
        new_dir = np.where(guess_ok, day_sign, -day_sign).astype(np.int8)
        if p_win == 0.5:
            new_dir = np.where(rng.random(P) < 0.5, 1, -1).astype(np.int8)

        for c in range(C):
            act = ~ruined[c]
            # otwarcie
            can_open = act & ~is_open[c] & (cash[c] * (1 + 1e-9) >= (M[c, 0] / LEVERAGE) * cash[c]) & (cash[c] > 0)
            if can_open.any():
                Nn = M[c, 0] * cash[c, can_open]
                Npos[c, can_open] = Nn
                margin[c, can_open] = Nn / LEVERAGE
                costs[c, can_open] = spread * Nn
                spread_total[c, can_open] += spread * Nn
                off[c, can_open] = 0.0
                hold[c, can_open] = 0
                direc[c, can_open] = new_dir[can_open]
                is_open[c, can_open] = True
            op = is_open[c] & act
            idx = np.flatnonzero(op)
            if idx.size == 0:
                pass
            else:
                hold[c, idx] += 1
                days_with_pos[c, idx] += 1
                N_ = Npos[c, idx]; d_ = direc[c, idx]; o_ = off[c, idx]; cs_ = costs[c, idx]
                A = (G + cs_) / N_                                   # próg celu jako ułamek nominału
                Lstop = cash[c, idx] - cs_ - STOPOUT_LVL * margin[c, idx]   # strata PnL wywołująca stop-out
                long = d_ > 0
                with np.errstate(divide="ignore", invalid="ignore"):
                    # progi w log-zwrocie dnia (L_j): u = pierwsze przebicie w górę, dn = w dół
                    u_long  = np.log1p(A) - o_
                    d_long  = np.where(Lstop / N_ < 1, np.log1p(-np.minimum(Lstop / N_, 0.999999)), -np.inf) - o_
                    u_short = np.log1p(Lstop / N_) - o_
                    d_short = np.log1p(-A) - o_
                if not stopout:
                    d_long = np.full_like(d_long, -np.inf); u_short = np.full_like(u_short, np.inf)
                u = np.where(long, u_long, u_short).astype(np.float32)
                dn = np.where(long, d_long, d_short).astype(np.float32)
                # najpierw tani test na ostatniej kolumnie (czy próg w ogóle przebity w tym dniu),
                # pełne szukanie pierwszego kroku tylko dla wierszy, które przebiły
                any_u = cmax[idx, -1] >= u
                any_d = cmin[idx, -1] <= dn
                j_u = np.full(idx.size, S + 1, np.int64); j_d = np.full(idx.size, S + 1, np.int64)
                if any_u.any():
                    ru = idx[any_u]
                    j_u[any_u] = (cmax[ru] >= u[any_u][:, None]).argmax(axis=1)
                if any_d.any():
                    rd_ = idx[any_d]
                    j_d[any_d] = (cmin[rd_] <= dn[any_d][:, None]).argmax(axis=1)
                first = np.minimum(j_u, j_d)
                closed = first <= S
                up_first = j_u <= j_d
                is_target = closed & ((long & up_first) | (~long & ~up_first))
                is_stop = closed & ~is_target
                if closed.any():
                    ci = idx[closed]; jj = first[closed]
                    Lj = L[ci, jj].astype(np.float64)
                    pnl = direc[c, ci] * Npos[c, ci] * np.expm1(off[c, ci] + Lj) - costs[c, ci]
                    tgt = is_target[closed]
                    pnl = np.where(tgt, np.maximum(pnl, G), pnl)         # dyskretny krok: cel nie mniej niż G
                    cash_before = cash[c, ci].copy()
                    if DEBUG:
                        DEBUG_LOG.extend(zip([t]*len(ci), ci.tolist(), tgt.tolist(), pnl.tolist(), cash[c, ci].tolist(), Npos[c, ci].tolist(), hold[c, ci].tolist()))
                        bad = (~tgt) & (pnl < -1.5 * cash[c, ci])
                        for k in np.flatnonzero(bad)[:3]:
                            print("DEBUG t", t, "path", ci[k], "cash", cash[c, ci[k]], "N", Npos[c, ci[k]], "dir", direc[c, ci[k]],
                                  "off", off[c, ci[k]], "Lj", Lj[k], "costs", costs[c, ci[k]], "hold", hold[c, ci[k]],
                                  "pnl", pnl[k], "j_u", j_u[closed][k], "j_d", j_d[closed][k], "u", u[closed][k], "dn", dn[closed][k],
                                  "cmax_end", cmax[ci[k], -1], "cmin_end", cmin[ci[k], -1], "margin", margin[c, ci[k]])
                    cash[c, ci] += pnl
                    n_trades[c, ci] += 1
                    sum_realized[c, ci] += pnl
                    worst_trade[c, ci] = np.minimum(worst_trade[c, ci], pnl)
                    worst_ratio[c, ci] = np.minimum(worst_ratio[c, ci], pnl / cash_before)
                    tci = ci[tgt]
                    n_target[c, tci] += 1
                    sum_target_hold[c, tci] += hold[c, tci]
                    sum_target_pnl[c, tci] += pnl[tgt]
                    hold_hist[c] += np.bincount(hold[c, tci], minlength=n_days + 2)[: n_days + 2]
                    sci = ci[~tgt]
                    n_stop[c, sci] += 1
                    stop_loss_total[c, sci] += pnl[~tgt]
                    is_open[c, ci] = False
                # pozycje nadal otwarte: przeniesienie na następny dzień + swap za noc
                oi = idx[~closed]
                if oi.size:
                    off[c, oi] += L_end[oi]
                    costs[c, oi] += swap * Npos[c, oi]
                    swap_total[c, oi] += swap * Npos[c, oi]
            # kapitał na koniec dnia
            eq = cash[c].copy()
            oi_all = np.flatnonzero(is_open[c] & act)
            if oi_all.size:
                eq[oi_all] += direc[c, oi_all] * Npos[c, oi_all] * np.expm1(off[c, oi_all]) - costs[c, oi_all]
            equity[c, act] = eq[act]
            if ruin_stop:
                newly = act & (eq < RUIN_LVL * K0[c, 0])
                if newly.any():
                    ruined[c, newly] = True
                    ruin_day[c, newly] = t
                    # zamrożenie: pozycja rozliczona po kursie z końca dnia (liczona jako zrealizowana strata)
                    loss = eq[newly] - cash[c, newly]
                    worst_trade[c, newly] = np.minimum(worst_trade[c, newly], loss)
                    worst_ratio[c, newly] = np.minimum(worst_ratio[c, newly], loss / cash[c, newly])
                    cash[c, newly] = eq[newly]
                    is_open[c, newly] = False
            if (t + 1) % DAYS_PER_YEAR == 0:
                equity_year[c, (t + 1) // DAYS_PER_YEAR] = equity[c]
        if store_ref:
            ref_daily[t + 1] = equity[ref_idx, :N_STORE_PATHS]
        if verbose and (t + 1) % 250 == 0:
            print(f"    sigma={sigma_d:.3%} p={p_win} dzień {t+1}/{n_days}  {time.time()-t0:.0f}s", flush=True)

    # ----- metryki
    res = {}
    for c, (K, m) in enumerate(combos):
        yrs = n_days // DAYS_PER_YEAR
        y1 = equity_year[c, 1] - K if yrs >= 1 else equity[c] - K
        tot = equity[c] - K
        dwp = days_with_pos[c].sum()
        nt = n_target[c].sum()
        hist = hold_hist[c]
        cum = np.cumsum(hist)
        med_hold = int(np.searchsorted(cum, cum[-1] / 2) ) if cum[-1] > 0 else None
        p95_hold = int(np.searchsorted(cum, 0.95 * cum[-1])) if cum[-1] > 0 else None
        max_hold = int(np.max(np.nonzero(hist)[0])) if cum[-1] > 0 else None
        r1 = ruined[c] & (ruin_day[c] < DAYS_PER_YEAR)
        res[f"K{K}_m{m}"] = dict(
            K=K, m=m, N_start=m * K, margin_start=m * K / LEVERAGE, margin_pct_of_capital=100 * m / LEVERAGE,
            target_pct_of_days_with_pos=100 * nt / dwp if dwp else None,
            mean_hold_days=float(sum_target_hold[c].sum() / nt) if nt else None,
            median_hold_days=med_hold, p95_hold_days=p95_hold, max_hold_days=max_hold,
            mean_daily_result_y1=float(y1.mean() / DAYS_PER_YEAR),
            median_year1=float(np.median(y1)), p5_year1=float(np.percentile(y1, 5)), mean_year1=float(y1.mean()),
            p_positive_year1=float((y1 > 0).mean()),
            p_ruin_1y=float(r1.mean()), p_ruin_5y=float(ruined[c].mean()) if yrs >= 5 else None,
            worst_single_loss_zl=float(worst_trade[c].min()), worst_single_loss_pct=float(100 * worst_trade[c].min() / K),
            worst_single_loss_pct_of_account=float(100 * worst_ratio[c].min()), median_path_worst_loss_pct_of_account=float(100 * np.median(worst_ratio[c])),
            median_path_worst_loss_zl=float(np.median(worst_trade[c])),
            mean_per_trade_all=float(sum_realized[c].sum() / max(n_trades[c].sum(), 1)),
            mean_per_trade_target=float(sum_target_pnl[c].sum() / max(nt, 1)),
            mean_stopout_loss_zl=float(stop_loss_total[c].sum() / max(n_stop[c].sum(), 1)) if n_stop[c].sum() else None,
            n_stopouts_per_path=float(n_stop[c].mean()),
            trades_per_path=float(n_trades[c].mean()),
            mean_total_result=float(tot.mean()), median_total_result=float(np.median(tot)),
            spread_cost_per_path=float(spread_total[c].mean()), swap_cost_per_path=float(swap_total[c].mean()),
            mean_ruin_day=float(ruin_day[c][ruined[c]].mean()) if ruined[c].any() else None,
            hold_hist=hist.tolist() if (K, m) == (REF["K"], REF["m"]) else None,
        )
    out = dict(results=res, combos=combos, n_paths=P, n_days=n_days, sigma=sigma_d, p_win=p_win,
               spread=spread, swap=swap, stopout=stopout, ruin_stop=ruin_stop, seconds=time.time() - t0)
    out["equity_year"] = equity_year
    if store_ref:
        out["ref_daily"] = ref_daily
        out["ref_ruined"] = ruined[ref_idx, :N_STORE_PATHS]
        out["ref_ruin_day"] = ruin_day[ref_idx, :N_STORE_PATHS]
    return out


# ----------------------------------------------------------------------------- URUCHOMIENIE
def main():
    n_days = YEARS * DAYS_PER_YEAR
    runs = {}
    print(f"Ścieżek: {N_PATHS}, dni: {n_days}, kroków/dzień: {STEPS_PER_DAY}", flush=True)
    # główne przebiegi: 3 zmienności przy 50/50 oraz 52/48 przy sigma 1 %
    for label, sig in SIGMAS.items():
        for p in ([0.5, 0.52] if label == "1%" else [0.5]):
            key = f"sigma{label}_p{p}"
            print(f"== {key}", flush=True)
            runs[key] = simulate(sig, p, N_PATHS, n_days, CAPITALS, MULTS, SPREAD, SWAP,
                                 store_ref=REF if (label == REF["sigma"] and p == 0.5) else None)
    # wrażliwość na koszty: 2x i 0,5x (sigma 1 %, 50/50, 1 rok — żeby zmieścić się w czasie)
    for fac in (2.0, 0.5):
        key = f"sens_cost_x{fac}"
        print(f"== {key}", flush=True)
        runs[key] = simulate(SIGMAS["1%"], 0.5, N_PATHS, DAYS_PER_YEAR, CAPITALS, MULTS, SPREAD * fac, SWAP * fac)
    # test poprawności: zero kosztów, bez stop-outu i bez zatrzymania po ruinie -> średni wynik dzienny ~ 0
    print("== sanity", flush=True)
    runs["sanity"] = simulate(SIGMAS["1%"], 0.5, N_PATHS, DAYS_PER_YEAR, [REF["K"]], [2, 5, 20], 0.0, 0.0,
                              stopout=False, ruin_stop=False)

    # ----- zapis JSON
    dump = {k: {kk: vv for kk, vv in v.items() if kk not in ("ref_daily", "ref_ruined", "ref_ruin_day", "equity_year")}
            for k, v in runs.items()}
    dump["params"] = dict(N_PATHS=N_PATHS, STEPS_PER_DAY=STEPS_PER_DAY, DAYS_PER_YEAR=DAYS_PER_YEAR, YEARS=YEARS, G=G,
                          CAPITALS=CAPITALS, MULTS=MULTS, LEVERAGE=LEVERAGE, STOPOUT_LVL=STOPOUT_LVL, RUIN_LVL=RUIN_LVL,
                          SPREAD=SPREAD, SWAP=SWAP, SIGMAS=SIGMAS, SEED=SEED, sizing="N = m * kapitał w chwili otwarcia")
    with open(os.path.join(OUT_DIR, "wyniki_wariant_b.json"), "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)

    # ----- tabele markdown
    def fz(x, d=0):
        return "—" if x is None else f"{x:,.{d}f}".replace(",", " ")
    def pc(x, d=1):
        return "—" if x is None else f"{100*x:.{d}f} %"
    md = []
    md.append("# Wariant B „bez stop lossa, trzymam do +100 zł” — wyniki Monte Carlo\n")
    md.append(f"Ścieżek: {N_PATHS}, dni: {n_days} (5 lat po 250), krok 1 min (480/dzień), cel {G:.0f} zł/dzień, "
              f"spread {SPREAD:.2%} N, swap {SWAP:.2%} N/noc, depozyt N/20, stop-out przy 50 % depozytu, ruina < 50 % startu. "
              f"Nominał N = m × kapitał w chwili otwarcia (depozyt = m/20 kapitału; m=20 ⇒ 100 % kapitału w depozycie).\n")
    for key, r in runs.items():
        if key.startswith("sens") or key == "sanity":
            continue
        md.append(f"\n## σ = {r['sigma']:.1%}/dzień, trafność kierunku {r['p_win']:.0%}\n")
        md.append("| Kapitał | m | Nominał start | Dni z celem* | Śr. trzymanie [dni] | Mediana trzymania | Śr. wynik/dzień (1 rok) | Mediana rok 1 | 5. perc. rok 1 | P(ruina 1 r.) | P(ruina 5 l.) | Najw. strata [zł] | Najw. strata [% kap. start.] | Najw. strata [% konta w chwili straty] | Śr./transakcję ze stop-outami |")
        md.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for ck, v in r["results"].items():
            md.append(f"| {fz(v['K'])} | {v['m']} | {fz(v['N_start'])} | {fz(v['target_pct_of_days_with_pos'],1)} % | "
                      f"{fz(v['mean_hold_days'],1)} | {fz(v['median_hold_days'])} | {fz(v['mean_daily_result_y1'],1)} | "
                      f"{fz(v['median_year1'])} | {fz(v['p5_year1'])} | {pc(v['p_ruin_1y'])} | {pc(v['p_ruin_5y'])} | "
                      f"{fz(v['worst_single_loss_zl'])} | {fz(v['worst_single_loss_pct'],0)} % | {fz(v['worst_single_loss_pct_of_account'],0)} % | {fz(v['mean_per_trade_all'],1)} |")
        md.append("\n\\* odsetek dni z otwartą pozycją, w których osiągnięto cel.")
    md.append("\n## Wrażliwość na koszty (σ = 1 %, 50/50, 1 rok)\n")
    md.append("| Kapitał | m | Koszt ×0,5: śr./dzień | Koszt ×1: śr./dzień | Koszt ×2: śr./dzień | ×0,5: P(ruina 1 r.) | ×1 | ×2 | ×0,5: mediana rok 1 | ×1 | ×2 |")
    md.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    base = runs["sigma1%_p0.5"]["results"]
    for ck in base:
        v1, v2, v05 = base[ck], runs["sens_cost_x2.0"]["results"][ck], runs["sens_cost_x0.5"]["results"][ck]
        md.append(f"| {fz(v1['K'])} | {v1['m']} | {fz(v05['mean_daily_result_y1'],1)} | {fz(v1['mean_daily_result_y1'],1)} | {fz(v2['mean_daily_result_y1'],1)} | "
                  f"{pc(v05['p_ruin_1y'])} | {pc(v1['p_ruin_1y'])} | {pc(v2['p_ruin_1y'])} | {fz(v05['median_year1'])} | {fz(v1['median_year1'])} | {fz(v2['median_year1'])} |")
    md.append("\n## Test poprawności (K = 25 000, σ = 1 %, 50/50, zero kosztów, bez stop-outu, bez zatrzymania po ruinie, 1 rok)\n")
    md.append("| m | Dni z celem | Śr. wynik/dzień | Mediana rok 1 | Średnia rok 1 | 5. perc. rok 1 | Śr. trzymanie [dni] |")
    md.append("|---:|---:|---:|---:|---:|---:|---:|")
    for ck, v in runs["sanity"]["results"].items():
        md.append(f"| {v['m']} | {fz(v['target_pct_of_days_with_pos'],1)} % | {fz(v['mean_daily_result_y1'],2)} | {fz(v['median_year1'])} | {fz(v['mean_year1'])} | {fz(v['p5_year1'])} | {fz(v['mean_hold_days'],1)} |")
    with open(os.path.join(OUT_DIR, "wyniki_wariant_b.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))

    # ----- wykresy
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.color": "#e6e6e3", "grid.linewidth": 0.8, "axes.axisbelow": True,
                         "axes.edgecolor": "#c3c2b7", "axes.labelcolor": "#0b0b0b", "figure.facecolor": "#fcfcfb",
                         "axes.facecolor": "#fcfcfb"})
    tys = FuncFormatter(lambda x, _: f"{x/1000:.0f} tys." if abs(x) >= 1000 else f"{x:.0f}")

    ref = runs["sigma1%_p0.5"]
    K, m = REF["K"], REF["m"]
    daily = ref["ref_daily"]; rr = ref["ref_ruined"]; rd = ref["ref_ruin_day"]
    # wybór 3 ścieżek: ruina wcześnie, ruina późno, przetrwała (lub najlepsza)
    order = np.argsort(np.where(rr, rd, 10**9))
    ruined_idx = [i for i in order if rr[i]]
    surv = [i for i in range(daily.shape[1]) if not rr[i]]
    picks = []
    if ruined_idx:
        picks.append(("Ścieżka A — ruina w %d. roku (kapitał < 50 %% startu)" , ruined_idx[len(ruined_idx)//10]))
        picks.append(("Ścieżka B — ruina w %d. roku (kapitał < 50 %% startu)", ruined_idx[int(len(ruined_idx)*0.8)]))
    if surv:
        ends = daily[-1, surv]; med_i = int(np.argsort(ends)[len(ends)//2])
        picks.append((f"Ścieżka C — przetrwała 5 lat (typowa z {100*len(surv)/daily.shape[1]:.0f} % ocalałych)", int(surv[med_i])))
    else:
        picks.append(("Ścieżka C — najpóźniejsza ruina (%d. rok)", ruined_idx[-1]))
    res_ref_ruin = ref["results"][f"K{K}_m{m}"]["p_ruin_5y"]
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    x = np.arange(daily.shape[0]) / DAYS_PER_YEAR
    styles = [(C_RED, "-"), (C_RED, "--"), (C_GREEN, "-")]
    for (lab, i), (col, ls) in zip(picks, styles):
        y = daily[:, i].copy()
        if rr[i]:
            y[rd[i] + 2:] = np.nan
            lab = lab % (rd[i] // DAYS_PER_YEAR + 1)
        ax.plot(x, y, color=col, ls=ls, lw=2, label=lab)
        if rr[i]:
            ax.plot(x[rd[i] + 1], y[rd[i] + 1], "o", color=col, ms=9, mec="#fcfcfb", mew=1.5)
            ax.annotate("ruina", (x[rd[i] + 1], y[rd[i] + 1]), textcoords="offset points", xytext=(6, -3), fontsize=9, color=col)
    ax.axhline(K, color=C_BLUE, lw=1.5, ls=":", label="Kapitał startowy 25 000 zł")
    ax.axhline(RUIN_LVL * K, color=C_GRAY, lw=1, ls=":", label="Próg ruiny (50 % startu)")
    ax.set_xlabel("Czas [lata]"); ax.set_ylabel("Kapitał na rachunku [zł]")
    ax.yaxis.set_major_formatter(tys)
    ax.set_title(f"Walec parowy: schodki po 100 zł w górę i przepaść — 3 przykładowe ścieżki\n"
                 f"kapitał 25 000 zł, nominał 5×, σ = 1 %/dzień, bez stop lossa, 5 lat", loc="left")
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.set_xlim(0, YEARS); ax.set_ylim(bottom=0)
    ax.text(0.99, 0.02, f"W 5 lat ruina spotyka {100*res_ref_ruin:.0f} % ścieżek; zielona to mediana ocalałych, nie najlepszy przypadek",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=8.5, color=C_GRAY)
    fig.tight_layout(); fig.savefig(os.path.join(OUT_DIR, "wykres1_sciezki_kapitalu.png")); plt.close(fig)

    # wykres 2: P(ruina 5 lat) vs kapitał dla m=2/5/20, sigma 1 %
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    res = ref["results"]
    mk = {2: ("o", "-"), 5: ("s", "--"), 20: ("^", ":")}
    for mm in MULTS:
        ys = [100 * res[f"K{Kc}_m{mm}"]["p_ruin_5y"] for Kc in CAPITALS]
        ax.plot(range(len(CAPITALS)), ys, color=C_RED, marker=mk[mm][0], ls=mk[mm][1], lw=2, ms=8, label=f"nominał {mm}× kapitału (depozyt {100*mm/LEVERAGE:.0f} % kapitału)")
        above = mm != 5
        ax.annotate(f"{mm}×", (len(CAPITALS) - 1, ys[-1]), textcoords="offset points", xytext=(10, 4 if above else -12), color=C_RED, fontsize=10)
        for xi, yv in enumerate(ys):
            ax.annotate(f"{yv:.0f} %", (xi, yv), textcoords="offset points", xytext=(0, 9 if above else -16), ha="center", fontsize=8, color=C_GRAY)
    ax.set_xticks(range(len(CAPITALS))); ax.set_xticklabels([f"{Kc:,}".replace(",", " ") for Kc in CAPITALS])
    ax.set_xlabel("Kapitał startowy [zł]"); ax.set_ylabel("Prawdopodobieństwo ruiny w 5 lat [%]")
    ax.set_ylim(0, 108)
    ax.set_title("Szansa utraty połowy kapitału w ciągu 5 lat (σ = 1 %/dzień, bot bez przewagi 50/50)\n"
                 "cel 100 zł/dzień, bez stop lossa — im większa dźwignia, tym pewniejsza przepaść", loc="left")
    ax.legend(loc="lower left", frameon=False, fontsize=9)
    fig.tight_layout(); fig.savefig(os.path.join(OUT_DIR, "wykres2_ruina_vs_kapital.png")); plt.close(fig)

    # wykres 3: histogram czasu trzymania do celu (log Y)
    hist = np.array(res[f"K{K}_m{m}"]["hold_hist"])
    nz = np.nonzero(hist)[0]
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    maxd = int(nz.max()) if nz.size else 1
    edges = np.unique(np.concatenate([[1, 2, 3, 4, 5, 6, 8, 11, 16, 21, 31, 51, 76, 101, 151, 251, 501, 1001, 1251], [maxd + 1]]))
    edges = edges[edges <= maxd + 1]
    if edges[-1] != maxd + 1: edges = np.append(edges, maxd + 1)
    counts = np.array([hist[a:b].sum() for a, b in zip(edges[:-1], edges[1:])])
    labels = [f"{a}" if b - a == 1 else f"{a}–{b-1}" for a, b in zip(edges[:-1], edges[1:])]
    cols = [C_GREEN if a == 1 else (C_RED if a >= 21 else C_GRAY) for a in edges[:-1]]
    bars = ax.bar(range(len(counts)), np.maximum(counts, 0.5), color=cols, width=0.8, edgecolor="#fcfcfb", linewidth=2)
    tot = counts.sum()
    for i, cnt in enumerate(counts):
        if cnt > 0:
            ax.annotate(f"{100*cnt/tot:.2f} %" if cnt / tot < 0.01 else f"{100*cnt/tot:.1f} %", (i, cnt), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=8, color=C_GRAY)
    ax.set_yscale("log"); ax.set_xticks(range(len(counts))); ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_xlabel("Dni trzymania pozycji, zanim pokazała +100 zł"); ax.set_ylabel("Liczba transakcji (skala logarytmiczna)")
    ax.set_title("Ile dni trzeba czekać na +100 zł? Zwykle jeden — ale ogon ciągnie się miesiącami\n"
                 "kapitał 25 000 zł, nominał 5×, σ = 1 %/dzień, " + f"{tot:,}".replace(",", " ") + " transakcji zakończonych celem (bez tych, które skończyły się ruiną)", loc="left")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=C_GREEN, label="cel tego samego dnia"), Patch(color=C_GRAY, label="2–20 dni"),
                       Patch(color=C_RED, label="≥ 21 dni (kapitał zamrożony, swapy rosną)")], frameon=False, fontsize=9)
    fig.tight_layout(); fig.savefig(os.path.join(OUT_DIR, "wykres3_czas_trzymania.png")); plt.close(fig)
    print("Gotowe. Czas przebiegów [s]:", {k: round(v["seconds"]) for k, v in runs.items()})


if __name__ == "__main__":
    main()
