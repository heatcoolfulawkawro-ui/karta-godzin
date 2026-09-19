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
  `karta_godzin_v3_{rok}_{miesiąc}`; dzień = `{dayType, urlopKomentarz, nocna,
  blocks:[{start,end,kind,komentarz,gapAfter}]}` (`nocna` opcjonalne). Zmiana formatu = migracja istniejących
  danych w Arkuszu, nie rób tego mimochodem.
- `localStorage` to natychmiastowy bufor, `fetch` do Arkusza idzie w tle —
  appka ma działać offline.
- Na iOS działają tylko prawdziwe natywne elementy: `<input type="file">`
  i realne dotknięcie `<a href>`. Programowe `.click()` na ukrytym linku
  i `location.href = dataURI` są zawodne (link z data: URI nie robi nic — potwierdzone
  przez Szefa na iPhonie). Od v1.2.1 eksport na iOS idzie przez natywne okno
  udostępniania (`navigator.share` z plikiem → „Zapisz w Plikach"), a gdy go brak —
  dwuetapowy link blob:. Od v1.2.2 (Chrome iOS nie ma udostępniania plików i psuje blob:) eksport idzie przez
  Apps Script: appka POSTuje plik (action:export, base64) → `exportXlsx_` w Kod.gs zapisuje go w folderze
  Dysku „KG-eksport-tmp" (dostęp: każdy z linkiem, sprzątanie plików starszych niż 15 min) i zwraca
  link uc?export=download. Wymagało jednorazowej autoryzacji Dysku (funkcja `authorizeDrive`).
  Nazwa pliku: M_RR_SKRÓT.xlsx (stała `USER_INITIALS`, dziś PF; po logowaniu z konta serwisanta).
- W polach czasu/komentarza nie przebudowuj DOM-u przy wpisywaniu (gubi
  fokus na telefonie) — odświeżaj tylko wyliczane etykiety.
- Paleta i styl: ciemny motyw, amber = akcja, zielony/czerwony = semantycznie
  (nadgodziny/niedobór), niebieski = wartości referencyjne.

## Funkcje (stan: 19.09.2026, v1.2)

- Dzień = Praca albo Urlop (z komentarzem). Praca: bloki czasu z kategorią —
  Biuro, Obiekt, Wizja, Organizacja, Dojazd; komentarz per blok. Organizacja
  ma własny wiersz i przełącznik w podsumowaniu; Dojazd zawsze liczy się do
  godzin pracy.
- Blok Dojazd: start wypełnia się końcem poprzedniego bloku. Luka między
  blokami = dojazd (liczy się do pracy) albo przerwa (nie liczy się).
- Do 12 bloków na dzień (`MAX_BLOCKS`). Bloki z wpisanym od–do zwijają się w
  paski (kategoria, godziny, czas, początek komentarza); dotknięcie paska
  rozwija pełną edycję. Stan zwinięcia (`blockUI`) jest tylko na ekranie —
  nie trafia do zapisywanych danych.
- Walidacja godzin: start wcześniejszy niż koniec poprzedniego wpisu (albo
  koniec nie później niż start) = błąd — wpis ma czerwoną ramkę i komunikat,
  NIE jest liczony, a dzień dostaje ⚠. Przejście przez północ (21:00–03:00)
  jest dozwolone tylko gdy dzień ma włączone „🌙 Praca w nocy" (pole `nocna` w
  danych dnia; `computeEffectiveBlocks(blocks, allowWrap)`). Dni zapisane przed
  v1.2, które korzystały z automatycznego zawijania, dostają `nocna:true` przy
  wczytaniu (`legacyNightFlag`), więc stare sumy się nie zmieniają.
- Wstawianie wpisów: „＋ wstaw wpis" między wpisami (start = koniec
  poprzedniego) oraz „＋ wpis" w linijce luki — zamienia lukę na gotowy wpis
  Dojazd od–do (suma bez zmian). „+ kolejny przedział" też startuje od końca
  poprzedniego wpisu. Luka: 🚗 Dojazd (liczona) albo ☕ Przerwa (nie liczona).
- Widoki Dzień / Tydzień / Miesiąc; strzałki `‹ ›` przesuwają o 1 dzień /
  7 dni / 1 miesiąc; dotknięcie dnia otwiera pełną kartę edycji.
- Panel (kompaktowy): suma, norma, nadgodziny/niedobór, efektywność %, dni z
  wpisem, urlop X/26 oraz średnia dzienna z przełącznikiem (dotknięcie
  kafelka, zapamiętywane w ustawieniach jako `avgMode`): `norma` = cała suma
  godzin / normatywne dni robocze od 1. do ostatniego dnia z wpisem (bez
  weekendów, świąt i urlopów) — do porównania z 8:00; `real` = cała suma /
  wszystkie dni z wpisem (soboty, niedziele i święta też).
- Podsumowanie miesiąca to osobna strona na pełny ekran z „‹ Wróć"; przy
  każdej strefie % udziału w przepracowanym czasie (5 stref = 100%) i pasek.
- Święta ustawowe: stałe daty + liczone z Wielkanocy (algorytm
  Meeusa/Jonesa/Butchera; Pon. Wielkanocny = +1, Boże Ciało = +60).
- Zakładka Urlopy (rok): lista dni + wykres słupkowy per miesiąc.
- Import/eksport .xlsx (SheetJS z CDN). Bloki połączone dojazdem sklejają się
  w jeden przedział, rozdziela je tylko przerwa. Szablon ma 8 par od–do
  (`XLSX_SPAN_COLS`): stare B–G oraz dodatkowe U–AD za kolumną „Uwagi", żeby
  stary układ i stare pliki dalej działały; formuła w H sumuje wszystkie.
- Światełko statusu połączenia z Arkuszem (ping przy starcie).

## Otwarte tematy

- v1.1 i v1.2 wdrożone 19.09.2026 — czekają na uwagi Szefa z używania w terenie
  (zwijane bloki, wstawianie wpisów, walidacja godzin + „Praca w nocy", średnia
  dzienna, podsumowanie z %).
- Strona „Podsumowanie miesiąca" ma być dalej rozbudowywana (życzenie: wykresy,
  „gdzie mogę coś urwać"); dziś ma % i paski per strefa.
- Górny nagłówek (zakładki + widoki + panel) zajmuje na telefonie dużo ekranu —
  propozycja: zwijanie przy przewijaniu. Nieustalone, wymaga makiety.
- Średnia dzienna jest w formacie g:mm; Szef w rozmowie użył zapisu dziesiętnego
  (9,67) — zapytać, czy dopisać obok.
- Statystyki per rok (agregacja miesięcy), a potem panel osobno per serwisant —
  dziś appka obsługuje jedną osobę. Warunek Szefa: najpierw dopracować wygląd u
  niego, dopiero potem powielać. Wymaga ustaleń (kto widzi czyje godziny, osobne
  dane per osoba, ewentualny PIN) i makiety przed kodowaniem.
- Eksport .xlsx na iPhonie: DZIAŁA od v1.2.2 (potwierdził Szef 19.09.2026, Chrome iOS).
- KOPIE ZAPASOWE (19.09.2026): backend ma akcję `backup` (tylko odczyt; klucz kopii — jego SHA-256 jest w Kod.gs,
  sam klucz tylko na PC, zaszyfrowany kontem Windows (DPAPI): F:AI_backupy_configkey.dat, tam też backup.log) oraz `admin.backup`
  (pod zalogowanego admina — do przycisku „Pobierz kopię” na telefonie, etap 2). Zrzut = wszystkie wiersze Data (dane
  wszystkich kont), konta BEZ hashy/soli (po odtworzeniu resetuje się PIN-y), dziennik zmian; bez sesji i pepperu.
  Skrypt `tools/backup-karta-godzin.ps1` zapisuje JSON + SHA-256 w F:AI_backupykarta-godzin (poza repo — dane
  osobowe!), kopiuje na H:BACKUP-karta-godzin i na dysk zewnętrzny o etykiecie KOPIE*/ZEWN*, gdy jest podpięty; niczego
  nie kasuje. UWAGA: C, F, G, H, I to partycje JEDNEGO fizycznego SSD — H: nie chroni przed awarią dysku; potrzebny dysk
  zewnętrzny (BitLocker, odłączany). Zadanie Harmonogramu `KartaGodzin-Backup` (20:00 + przy logowaniu, za zgodą Szefa) działa;
  klucz i log NIE mogą leżeć w %LOCALAPPDATA% — pakiet aplikacji Claude wirtualizuje tam zapisy i Harmonogram widzi
  inne pliki (tak zepsuł się pierwszy test). Pierwsze pełne automatyczne pobranie: następny dzień. Odtwarzanie: wiersze data →
  zakładka Data (klucz, wartość), konta/PIN-y przez admin.createUser/setPin.
- KONTA I BEZPIECZEŃSTWO (w toku, etap 1 z 3): backend z logowaniem jest wdrożony (Kod.gs, wersja @6),
  ale NADAL działa też stary otwarty tryb (`LEGACY_OPEN = true`), bo frontend jeszcze nie ma
  logowania. Etap 2: frontend v1.3 (ekran logowania login=skrót + PIN 6 cyfr, token sesji 30 dni,
  przełącznik Karta/Admin, zakładka Admin, Zmień PIN, komunikat o ukrytym miesiącu) — najpierw makieta
  do akceptacji. Etap 3: `LEGACY_OPEN = false` i redeploy (zamyka otwarty dostęp).
  Backend: akcje POST {action, token,...}: login, logout, me, get, set, export, changePin oraz
  admin.list/createUser/setPin/setName/setVisibility/unlock/setActive/get. Konta w zakładce Users
  (PIN tylko jako HMAC z pepperem z właściwości skryptu; blokada 5 błędów → 5 min, podwajana do 24 h;
  Piotr/admin odblokowuje), sesje w zakładce Sessions (tylko SHA-256 tokenu). Dane: konto PF (admin,
  Paweł) ma stare klucze bez prefiksu (zero migracji); pozostali pod `ID::klucz`. Widoczność historii
  per konto (visibleMonths: 0=całość, 1=bieżący, 2, 3…) egzekwowana na serwerze (odczyt i zapis
  ukrytego miesiąca → błąd hidden; admin widzi wszystko; dane się nie kasują). Konta: PF (admin),
  PS = Piotr S (użytkownik testowy; PIN startowy słaby — do zmiany). Testy logiki: atrapa Apps Script
  (69 sprawdzeń). PIN-ów NIE zapisujemy w repo ani w CLAUDE.md.

## Więcej kontekstu

- Historia projektu, reguły liczenia czasu w firmie, format starego Excela,
  odrzucone podejścia i preferencje Szefa co do wyglądu:
  `../_wiedza-z-czatow/04-godziny-projekt-pf.md` (wyciąg ze starego czatu
  claude.ai, 26.08–18.09.2026). Przeczytaj przed większą zmianą.
- Żelazna zasada Szefa: pracujemy na poprzedniej wersji i ulepszamy tylko dany
  element — nie przebudowujemy całości i nie ruszamy tego, o co nie prosił.
- Po każdym wdrożeniu napisz wprost, co zostało wypchnięte i gdzie, co
  sprawdzone, a czego nie (np. „na iPhonie nie testowałem").
- Mapa wszystkich projektów i zasady porządku: `../CLAUDE.md`.
