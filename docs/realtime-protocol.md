# Realtime-протокол (WebSocket)

Контракт между iOS-клиентом и бэкендом пати-игры. REST-часть описана в OpenAPI (`/docs`);
здесь — всё, что идёт по WebSocket.

## 1. Жизненный цикл

```
POST /v1/guest                    {deviceId, nickname, avatarId}   → tokens + profile
POST /v1/rooms                    {modeId | null}                   → {code, wsPath, room}   (хост)
POST /v1/rooms/{code}/join                                          → {code, wsPath, room}   (игроки)
WS   /v1/ws/rooms/{code}          Authorization: Bearer <accessToken>
```

1. Войти в комнату по REST (`create` / `join`) — это добавляет игрока в список участников.
   Язык комнаты задаётся при создании: `{"locale": "en"}` в теле или заголовок
   `Accept-Language` (см. раздел «Язык контента»).
2. Открыть WebSocket `wss://<host>/v1/ws/rooms/{code}` с заголовком
   `Authorization: Bearer <accessToken>` (`URLSessionWebSocketTask` — через `URLRequest`).
3. Первым сообщением **всегда** приходит `room.snapshot` — полное персональное состояние комнаты.
4. Дальше приходят события комнаты в порядке `seq`. Команды клиент шлёт в тот же сокет.

Access-токен живёт 1 час: при переподключении с истёкшим токеном сокет закроется с `4401` —
обновите токен (`POST /v1/auth/refresh`) и подключитесь снова.

## 2. Формат сообщений

Клиент → сервер:

```json
{"type": "vote.cast", "msgId": "c-42", "data": {"choice": "<userId>"}}
```

`msgId` — любая строка клиента, возвращается в ответе. Неизвестные поля в `data` отбрасываются.

Сервер → клиент:

```json
{"type": "round.started", "seq": 17, "serverTime": 1790761465978, "data": {...}}
```

| Поле | Смысл |
|---|---|
| `seq` | Номер события в комнате (монотонный). У ответов `ack`/`error`/`pong` — `null`. |
| `serverTime` | Время сервера, epoch **миллисекунды**. |
| `data` | Полезная нагрузка. |

Ответ на каждую команду: `ack` `{msgId, type}` или `error` `{msgId, code, message}`.
События, вызванные командой, могут прийти **раньше** её `ack` — UI обновляйте по событиям,
а не по `ack`.

### Порядок и повторы

* `room.snapshot` содержит `seq` на момент снимка. События с `seq <= snapshot.seq` клиент
  игнорирует (они уже учтены в снимке).
* Команда `{"type": "room.resync"}` — прислать свежий `room.snapshot` (например, после
  возврата приложения из фона).

### Время и таймеры

Все дедлайны (`endsAt`, `revealAt`, `returnUntil`, `graceUntil`) — абсолютные epoch-мс сервера.
Смещение часов: `offset = serverTime - Date.now()` по любому сообщению (или `ping` → `pong`).
Отображаемый остаток: `endsAt - (Date.now() + offset)`. Таймеры **решает сервер** — клиент
только показывает обратный отсчёт; по истечении сервер сам пришлёт следующее событие.

### Служебное

* `{"type": "ping"}` → `pong` (`data.msgId`, `serverTime`) — для синхронизации часов.
* Лимиты: сообщение ≤ 8 КБ (иначе закрытие `1008`), ~10 команд/с (иначе `error rate_limited`).

## 3. Закрытие сокета

| Код | Причина | Что делать клиенту |
|---|---|---|
| `1000` | Штатное закрытие | — |
| `1008` | Нарушение протокола (слишком большое сообщение) | Исправить клиент |
| `1012` | Рестарт сервера | Переподключиться сразу |
| `1013` | Клиент не успевает читать | Переподключиться |
| `4000` | Игрок вышел (`room.leave`) | На главный экран |
| `4001` | Открыт более новый сокет этого же игрока | Не переподключаться |
| `4003` | Игрока выгнали | На главный экран; повторный вход запрещён |
| `4004` | Комнаты нет или игрок не участник | На главный экран |
| `4401` | Токен не прошёл проверку | Обновить токен, переподключиться |

**Переподключение.** Разрыв сети — не выход: у игрока `RECONNECT_GRACE_SECONDS` (30 с), чтобы
вернуться. Остальные видят `player.disconnected` → `player.connected`. Переподключение — это
просто новый сокет, после которого приходит свежий `room.snapshot`. Если игрок не вернулся:
в лобби он удаляется (`player.left reason=timeout`), в игре помечается неактивным (ход и
голосование его пропускают), а после игры удаляется. Рекомендуемый backoff: 0.5 → 1 → 2 → 4 с.

## 4. Снимок комнаты (`room.snapshot`)

```json
{
  "code": "X7B2",
  "hostId": "<userId>",
  "status": "lobby | playing",
  "mode": {"id": 1, "slug": "most_likely", "kind": "question_list", "title": "...", "minPlayers": 3, "maxPlayers": 12, "locale": "ru"} | null,
  "locale": "ru",
  "categories": ["friendly"],
  "settings": {"voteSec": 15},
  "maxPlayers": 12,
  "players": [{"userId": "...", "nickname": "Alice", "avatarId": 3, "avatarKey": "avatar_03",
               "isHost": true, "connected": true, "active": true}],
  "seq": 17,
  "game": null | {"kind": "...", "mode": {...}, "phase": "...", "phaseId": 4, "endsAt": 1790761485976,
                  "round": 2, "settings": {...}, "scores": {"<userId>": 1}, ...поля игры...}
}
```

`mode: null` — игра будет выбрана случайно при старте. `categories: []` — все категории.
Поле `game` — персональное: скрытые данные (роль импостера, чужие варианты ответов, авторы
анонимных ответов) в нём не появляются.

«Игрок» в событиях (`brief`): `{"userId", "nickname", "avatarId", "avatarKey"}`.

## 5. Лобби

| Команда | Кто | `data` |
|---|---|---|
| `room.update_settings` | хост, в лобби | `modeId` (int или `null` = случайная), `locale` (`ru`/`en`), `categories` (`friendly`/`cringe`/`spicy`), `settings` (патч; `null` у ключа удаляет его) |
| `room.kick` | хост | `userId` |
| `room.transfer_host` | хост | `userId` |
| `room.leave` | любой | — |
| `game.start` | хост, в лобби | — (колоды и режим берутся из настроек комнаты) |
| `game.end` | хост, в игре | — |
| `game.next` | хост, в игре | — (универсальное «дальше»: закрыть фазу / следующий раунд) |

| Событие | `data` |
|---|---|
| `player.joined` | `{player}` |
| `player.connected` | `{userId}` |
| `player.disconnected` | `{userId, graceUntil}` |
| `player.left` | `{userId, reason: left | kicked | timeout | inactive}` |
| `host.changed` | `{hostId, reason: host_left | transferred}` — хост ушёл → хостом становится самый ранний онлайн-игрок |
| `room.settings` | `{mode, locale, categories, settings, maxPlayers}` |
| `game.started` | `{kind, mode, settings, categories, players}` |
| `game.finished` | `{reason: completed | host_ended | not_enough_players | no_content, kind, leaderboard, summary}` — комната возвращается в лобби |
| `room.closed` | `{}` — в комнате никого не осталось |

`leaderboard`: `[{userId, nickname, avatarId, avatarKey, score, rank}]` (пустой, если в игре нет
очков). В «Бомбе» и режиме с `scoring` очки — штрафные: меньше — лучше, порядок уже учтён.

## 5a. Язык контента

Поддерживаются `ru` и `en` (серверная настройка `SUPPORTED_LOCALES`). Язык есть у каждой игры
каталога (поле `locale`); карточки, слова «Импостера» и варианты фраз идут на языке игры.

* **Каталог** `GET /v1/modes`: `?locale=en` или заголовок `Accept-Language` (iOS отправляет его
  автоматически по языку устройства). Если игр на этом языке нет — отдаётся язык по умолчанию;
  фактический язык — в заголовке ответа `Content-Language`.
* **Комната** создаётся с языком из тела (`locale`) или `Accept-Language` хоста; видно в снимке
  (`locale`). При случайной игре (`mode: null`) игра и колоды выбираются на этом языке.
* **Выбор игры** (`modeId`) делает язык комнаты равным языку игры. Смена `locale` в лобби сбрасывает
  выбранную игру, если она на другом языке (`mode` станет `null`).
* Тексты интерфейса (кнопки, правила) локализует клиент; сервер отдаёт только контент.

## 6. Игры

`kind` из каталога (`GET /v1/modes`) определяет экран. Настройки — ключи `settings`
(значения по умолчанию указаны; хост меняет их в лобби, админ — `defaultSettings` игры).

### 6.1 `question_list` — список вопросов / «Кто вероятнее» (мин. 3)

Настройки: `roundsCount` (null = вся колода), `voteSec`=20, `autoAdvanceSec` (null = хост жмёт
«дальше»), `tiePolicy` = `random | all`, `scoring`=false, `allowSelfVote`=true.

Типы карточек (`card.type`): `yes_no` (варианты `"yes"`/`"no"`), `pick_player` (все игроки или
N случайных), `duel` (два случайных игрока), `dare` (голосованием выбирается цель, затем задание).

1. `round.started` `{round, roundsLeft, phase:"voting", endsAt, card{id,type,category,text,anonymous}, subject, options}` —
   `options`: `["yes","no"]` или список игроков. `{player}` в тексте уже заменён на ник `subject`.
2. Команда `vote.cast {choice}` (`"yes"`/`"no"` или `userId`); голос можно менять до конца фазы.
   Все видят `vote.progress {round, voted, total}`.
3. Когда проголосовали все онлайн-игроки или вышло время — `round.results`:
   `{counts, percentages, totalVotes, votes (null для анонимной карточки), targets[], tie, points, scores, majority (yes_no), dare?}`.
   Для `dare` дополнительно `dare.assigned {round, text, targets[]}` — цель видит задание как своё,
   остальные — кто его выполняет.
4. `game.next` (хост) или `autoAdvanceSec` → следующий `round.started`; колода кончилась → `game.finished`.

### 6.2 `wheel` — колесо фортуны (мин. 2)

Настройки: `collectSec`=180 (null = пока все не сдадут / хост), `spinDurationMs`=4000, `maxTextLen`=200.

1. `wheel.collecting {segments:["question","dare","gossip"], endsAt}`.
2. Каждый: `wheel.submit {question, dare, gossip}` (можно переотправить) → `wheel.progress {submitted, total}`.
3. Все сдали / время / хост `wheel.spin` → `wheel.ready {remaining{question,dare,gossip}}`.
4. Хост: `wheel.spin` → `wheel.spun {round, category, segmentIndex, segments, text, spinSeed, spinStartAt, durationMs, revealAt, remaining, remainingTotal}`.
   **Результат выбирает сервер.** Клиенты анимируют колесо детерминированно: стартуют в
   `spinStartAt`, крутятся `durationMs` и останавливаются на сегменте `segmentIndex`; число
   оборотов / смещение внутри сегмента берите из `spinSeed` — так у всех одинаковая анимация.
   Текст показывайте в `revealAt`. Автор скрыт.
5. Следующий `wheel.spin` доступен после `revealAt`. Спин при пустом колесе → `game.finished`.

### 6.3 `bomb` — бомба (мин. 2)

Настройки: `fuseMinSec`=40, `fuseMaxSec`=90 (длина фитиля случайна в диапазоне), `showTimer`=false,
`returnWindowSec`=5, `skipExplodeChance`=0.25, `roundsCount`=3.

* `bomb.round_started {round, roundsCount, holder, question, showTimer, endsAt (null если таймер скрыт)}`.
* Держатель: `bomb.pass` → `bomb.passed {from, to, question, returnUntil, reason}` (случайный другой игрок).
* Новый держатель до `returnUntil` может один раз `bomb.return` → `bomb.returned {from, to, question}`
  (предыдущий игрок сжульничал).
* Держатель: `bomb.skip` — другой вопрос; с вероятностью `skipExplodeChance` бомба взрывается
  сразу, иначе `bomb.question_changed {holder, question}`.
* `bomb.exploded {round, loser, reason: fuse | skip, scores, isLastRound}`; хост `game.next` →
  следующий раунд или `game.finished` (очки = число взрывов, меньше — лучше).

### 6.4 `impostor` — импостер (мин. 3)

Настройки: `impostorCount`=1 (меньше половины игроков), `hintForImpostor`=true, `speakSec`=30,
`voteSec`=30, `gamesCount`=3, `maxRounds`=3.

1. `impostor.game_started {gameNo, gamesCount, impostorCount, players}`.
2. **Приватно** каждому: `impostor.role {gameNo, role: civilian | impostor, word, hint}` —
   мирным слово, импостеру подсказка (или `null`).
3. `impostor.speaker {gameNo, round, speaker, order, index, endsAt}` — кто сейчас объясняет;
   `impostor.done_speaking` (говорящий или хост) или таймер → следующий.
4. `impostor.voting {round, candidates, endsAt}`; команда `impostor.vote {target}` (только живые,
   не за себя) → `vote.progress`.
5. `impostor.vote_result {votes, counts, tie, eliminated, wasImpostor, scores}` — ничья никого не
   исключает. Затем либо `impostor.game_over {winner: civilians | impostors, impostors, word, hint, scores, isLastGame}`,
   либо хост `game.next` → новый круг объяснений с тем же словом.
6. После `game_over` хост `game.next` → следующая игра или `game.finished` с таблицей лидеров.

Очки: мирный +1 за голос против импостера; победа мирных — +2 каждому мирному; победа
импостеров — +3 каждому импостеру.

### 6.5 `fill_blank` — допиши фразу (мин. 3)

Настройки: `answerMode` = `options | free`, `roundsCount`=5, `optionsPerPlayer`=5, `answerSec`=45,
`judgeSec`=30, `maxAnswerLen`=80, `autoAdvanceSec`.

1. `blank.round_started {round, roundsLeft, judge, prompt, answerMode, endsAt}` — судья по кругу.
2. `options`: **приватно** каждому не-судье `blank.options {round, options:[{id,text}]}` —
   у всех разные наборы. Команда `blank.submit {optionId}`. `free`: `blank.submit {text}`.
   Прогресс: `blank.progress {submitted, total}`.
3. `blank.judging {round, prompt, judge, answers:[{answerId, text}], endsAt}` — перемешано и
   анонимно. Судья: `blank.pick {answerId}` (таймаут — случайный выбор).
4. `round.results {winner, winningAnswer, filled, randomPick, answers:[{answerId, text, author}], scores}`.
5. `game.next` (хост) или `autoAdvanceSec` → следующий раунд; в конце — таблица лидеров.

### 6.6 `hot_seat` — «21 вопрос» (мин. 2)

Настройки: `roundsCount`=21, `turnSec` (null = без таймера).

`hotseat.turn {round, roundsCount, player, question, endsAt}` — случайный игрок (каждый по разу,
прежде чем кто-то повторится). Игрок (или хост) — `hotseat.done` → следующий ход. Без очков.

## 7. Коды ошибок команд

`not_host`, `not_in_room`, `not_in_game`, `not_your_turn`, `wrong_phase`, `no_game`,
`game_in_progress`, `room_full`, `kicked`, `not_enough_players`, `too_many_players`,
`invalid_vote`, `invalid_data`, `invalid_settings`, `no_content`, `mode_unavailable`,
`return_not_allowed`, `unknown_command`, `bad_message`, `rate_limited`, `busy` (повторите),
`room_not_found`, `internal_error`.

## 8. Как это устроено на сервере

* Состояние комнаты — один JSON-документ в Redis (`exposed:room:{code}`, TTL 6 ч).
* Любая команда: распределённый лок комнаты → загрузка → чистый движок правил
  (`app/domain/game`) → сохранение → публикация событий в Redis pub/sub. Поэтому сервис
  работает на любом числе воркеров и реплик, а клиенты одной комнаты могут сидеть на разных.
* Таймеры фаз и grace-периоды — Redis sorted set, забираются атомарно одним воркером; переживают
  рестарт процесса. Устаревший таймер (фаза уже сменилась) игнорируется.
* Колода загружается из PostgreSQL один раз при старте игры; завершённые игры архивируются в
  `game_sessions`.
