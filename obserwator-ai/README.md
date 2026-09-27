# Obserwator AI — jak przenieść na PC

Folder powstał w sesji w chmurze i czeka na gałęzi
`claude/ai-human-advocate-agent-c0j1gq` repozytorium `karta-godzin` — tylko po to,
żeby dało się go przenieść. Z kartą godzin nie ma nic wspólnego i nie trafia na `main`.

## 1. Przeniesienie (robi Claude na PC)

W Claude na PC, w folderze z projektami, wystarczy napisać:

> Przenieś folder `obserwator-ai` z gałęzi `claude/ai-human-advocate-agent-c0j1gq`
> repo karta-godzin do osobnego projektu obok karta-godzin, przeczytaj jego README
> i dopisz go do mapy projektów w CLAUDE.md.

Podpowiedź techniczna dla Claude na PC (w folderze karta-godzin):

```
git fetch origin claude/ai-human-advocate-agent-c0j1gq
git archive origin/claude/ai-human-advocate-agent-c0j1gq obserwator-ai | tar -x -C ..
```

Potem gałąź można usunąć — nic z niej nie trzeba scalać.

## 2. Przegląd co tydzień

- Ręcznie: otworzyć Claude w folderze `obserwator-ai` i wpisać `/przeglad-ai`.
- Automatycznie: zadanie zaplanowane w aplikacji Claude albo w Harmonogramie Windows
  (np. sobota rano), uruchamiające `/przeglad-ai` w tym folderze. Działa tylko, gdy
  PC jest włączony. Pliki zostają na dysku lokalnym w `przeglady/`.

## 3. Rozmowa

Otworzyć Claude w folderze `obserwator-ai` i pisać, np.:

- „Omów ostatni przegląd — co z tego jest naprawdę ważne?"
- „Bądź adwokatem diabła: przekonaj mnie, że przesadzam z niepokojem."
- „Co mówią sceptycy o …?"

## 4. Opcjonalnie: telefon

Jeśli przegląd ma przychodzić także na telefon (powiadomienie), można założyć osobne
prywatne repozytorium `obserwator-ai` na GitHubie: przegląd w chmurze co tydzień
zapisuje plik do repo i wysyła powiadomienie, a PC tylko pobiera (`git pull`) na
dysk lokalny.
