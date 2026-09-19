# Karta godzin — pamięć projektu

Mobilna appka webowa do wpisywania godzin pracy (RA-STER, HVAC/chłodnictwo),
zastępuje ręczne wklejanie do Excela. Używana głównie na iPhonie w terenie.
Z użytkownikiem rozmawiaj po polsku; to inżynier, nie programista — tłumacz
krótko pojęcia przy pierwszym użyciu i rób sam wszystko, co nie wymaga jego
logowania.

## Gdzie co leży

- **Frontend**: `index.html` (jeden plik: HTML + CSS + JS, bez frameworków,
  bez build stepu) → GitHub Pages:
  https://heatcoolfulawkawro-ui.github.io/karta-godzin/
- **Backend**: `Kod.gs` + `appsscript.json` → Google Apps Script podpięty do
  Arkusza „Karta godzin — dane" (zakładka `Data`, kolumny `key`, `value`).
  Prosty magazyn klucz-wartość przez `doGet`/`doPost`.
- **Web App URL** (stała `GAS_URL` w `index.html`) — NIE MOŻE się zmienić,
  telefony mają go w zapisanej appce.
- `.clasp.json` / `.claspignore` — konfiguracja clasp (wypychane są tylko
  `Kod.gs` i `appsscript.json`).

## Wdrażanie — wszystko przez `git push` na `main`

- **Frontend**: push → GitHub Pages publikuje samo (~1 min). Appka sama
  wykrywa nową wersję (HEAD + `last-modified`, skrypt na górze `index.html`)
  i przeładowuje się. Przy tytule jest znacznik wersji (`.vertag`) i stempel
  publikacji (`.buildtag`, format `DD.MM.RRRR GG:MM`) — przy każdym wydaniu
  frontendu ustaw stempel na bieżący czas, żeby na telefonie było widać,
  która wersja jest załadowana.
- **Backend**: push zmieniający `Kod.gs` lub `appsscript.json` uruchamia
  `.github/workflows/deploy-gas.yml`: `clasp push -f` + `clasp deploy
  --deploymentId <istniejące>` (sekret `CLASPRC_JSON`). Nigdy nie wdrażaj bez
  `--deploymentId` — powstałby nowy URL. Przepis i pułapki:
  `.claude/skills/gas-clasp-autodeploy/SKILL.md`.
- Po wdrożeniu backendu sprawdź: `gh run watch`, w logu `Deployed … @N` pod
  tym samym ID, oraz `GET <GAS_URL>?key=__ping__` → HTTP 200 z pustą treścią
  (pusta odpowiedź dla nieznanego klucza to sukces, nie błąd).
- `appsscript.json` pochodzi z `clasp pull` — nie edytuj z głowy; pola
  `webapp.access` / `executeAs` sterują dostępem do appki.

## Zasady przy zmianach

- Przed widoczną zmianą UI (nowy ekran/panel) pokaż makietę do akceptacji.
- Po każdej zmianie JS sprawdź składnię (wytnij `<script>` do pliku i
  `node --check`), zanim wypchniesz.
- POST do Apps Script zawsze z `Content-Type: text/plain;charset=utf-8` —
  `application/json` wywołuje preflight CORS, a przekierowanie 302 zamienia
  POST na GET i dane po cichu się nie zapisują.
- Format danych: JSON per miesiąc pod kluczem
  `karta_godzin_v3_{rok}_{miesiąc}`. Zmiana formatu = migracja istniejących
  danych w Arkuszu, nie rób tego mimochodem.
- `localStorage` to natychmiastowy bufor, `fetch` do Arkusza idzie w tle —
  appka ma działać offline.
- Na iOS działają tylko prawdziwe natywne elementy: `<input type="file">`
  i realne dotknięcie `<a href>`. Programowe `.click()` na ukrytym linku
  i `location.href = dataURI` są zawodne — stąd dwuetapowy eksport .xlsx.
- W polach czasu/komentarza nie przebudowuj DOM-u przy wpisywaniu (gubi
  fokus na telefonie) — odświeżaj tylko wyliczane etykiety.
- Paleta i styl: ciemny motyw, amber = akcja, zielony/czerwony = semantycznie
  (nadgodziny/niedobór), niebieski = wartości referencyjne.

## Funkcje (stan: 19.09.2026)

- Dzień = Praca albo Urlop (z komentarzem). Praca: bloki czasu z kategorią —
  Biuro, Obiekt, Wizja, Organizacja, Dojazd; komentarz per blok. Organizacja
  ma własny wiersz i przełącznik w podsumowaniu; Dojazd zawsze liczy się do
  godzin pracy.
- Blok Dojazd: start wypełnia się końcem poprzedniego bloku. Luka między
  blokami = dojazd (liczy się do pracy) albo przerwa (nie liczy się).
- Nocna zmiana wykrywana automatycznie (przejście przez północ).
- Widoki Dzień / Tydzień / Miesiąc; strzałki `‹ ›` przesuwają o 1 dzień /
  7 dni / 1 miesiąc; dotknięcie dnia otwiera pełną kartę edycji.
- Panel: suma, norma, nadgodziny/niedobór, efektywność %, dni z wpisem,
  urlop X/26.
- Święta ustawowe: stałe daty + liczone z Wielkanocy (algorytm
  Meeusa/Jonesa/Butchera; Pon. Wielkanocny = +1, Boże Ciało = +60).
- Zakładka Urlopy (rok): lista dni + wykres słupkowy per miesiąc.
- Import ze starego formatu .xlsx, eksport do .xlsx (SheetJS z CDN).
- Światełko statusu połączenia z Arkuszem (ping przy starcie).

## Otwarte tematy

- Panel per miesiąc/rok i osobno per serwisant — dziś appka obsługuje jedną
  osobę. Wymaga ustaleń (kto widzi czyje godziny, osobne dane per osoba,
  ewentualny PIN) i makiety przed kodowaniem.
