# Проверка исторических источников

Проверено 2026-08-17 через текущий сетевой маршрут. Здесь фиксируются только
контракты, которые были получены напрямую с официальных сайтов или из их frontend
bundle. Неизвестные источники не заменяются предполагаемыми endpoint.

## ESportsBattle eFootball — подтверждено

Официальный сайт: `https://football.esportsbattle.com/`.

Frontend использует относительный prefix `/api/`. Прямыми HTTP-запросами получены
JSON-ответы следующих маршрутов:

- `GET /api/participants?page={page}` — страницы участников;
- `GET /api/participants/{nickname}` — карточка игрока;
- `GET /api/participants/{nickname}/tournaments?page={page}` — турниры игрока;
- `GET /api/tournaments/{id}` — турнир;
- `GET /api/tournaments/{id}/matches` — матчи, участники, команды и счёт;
- `GET /api/tournaments/{id}/results` — итоговая таблица;
- `GET /api/tournaments/nearest-matches` — ближайшие матчи.

Контрольный завершённый турнир `252708` вернул 20 матчей. Матч `2226225`
содержит UTC-время, два nickname, команды, итоговый счёт и счёт предыдущего
периода. Поле `participant.id` меняется между турнирами и поэтому сохраняется как
ID участия в турнире, а не выдаётся за глобальный ID игрока. Для внешнего ключа
игрока используется nickname, потому что именно он является параметром официального
маршрута карточки игрока. До этапа normalization это только source-scoped identity.

API не сообщает точный timestamp settlement исторического матча. Такой timestamp
остаётся `NULL`; время скачивания RAW не подставляется вместо неизвестного факта.

## United Esports Leagues — подтверждено

Официальный frontend `https://efootball.unitedleagues.gg/` доступен. Его bundle
указывает API origin `https://api.unitedleagues.gg/`, sport `efootball` и реальные
POST-маршруты:

- `api/{sport}/load/tours/list`;
- `api/{sport}/load/tour/{id}`;
- `api/{sport}/load/tour/data/{id_sl}`;
- `api/{sport}/load/players/list`.

Прямой ответ списка на 2026-08-17 сообщил 8003 турнира. Контрольный турнир
`id=7981`, `id_sl=226610` вернул полный `tour_blocks.games` со стабильными ID
игроков и команд, временем, состоянием и счётом. Поле `country=CZ` описывает лигу,
но не timezone расписания. Сквозное сравнение свежей последовательности показало:
UEL `17:14 Cold–Prometh` соответствует Fonbet `14:04Z Cold–Prometh`, далее обе
последовательности идут с шагом 14 минут. Поэтому подтверждённый default —
`UEL_SOURCE_TIMEZONE=Europe/Moscow`; оставшиеся 10 минут являются систематическим
различием времени UEL и букмекерской линии. Настройка остаётся внешней. Timestamp
окончательного settlement не утверждается:
для результата фиксируется момент нашего получения ответа.

В `tour_blocks.games` есть также `updated_at`. На 3014 завершённых записях его
интерпретация как UTC дала timestamp строго после старта: минимум 7.05 минуты,
медиана 9.72, максимум 69.6 минуты. Все отрицательные разницы принадлежали только
будущим `upcoming`-матчам. Поле поэтому сохраняется отдельно как
`results.source_updated_at` и может служить консервативным моментом доступности
исторического результата. Оно намеренно не называется bookmaker settlement.

## SIS H2H Global Gaming League — подтверждено как отдельный источник

Официальная публикация SIS ведёт на `https://h2hggl.com/`. Текущий frontend
`v1.15.2-drive`, загруженный с `h2h.cdn-hudstats.com`, явно задаёт API origin
`https://api-h2h.hudstats.com/` и sport mapping:

- `esoccer -> fifa`;
- `ebasketball -> nba`;
- `eamericanfootball -> nfl`.

Из bundle подтверждены GET-маршруты:

- `v1/schedule/{sport}?date={timezone-aware ISO datetime}`;
- `v1/schedule/past/{sport}` и `v1/schedule/upcoming/{sport}`;
- `v1/participant/{sport}` и `v1/participant/{sport}/names`;
- `v1/participant/{sport}/stats?participant={name}`;
- `v1/h2h/{sport}?external_id={id}`;
- `v1/h2h/{sport}/participants?participant_a={a}&participant_b={b}`;
- `v1/match/stats/{sport}?external_id={id}`;
- `v1/timeline/?external_id={id}`;
- `v1/live/{sport}`.

Прямой дневной запрос eSoccer за 2026-08-16 вернул 371 матч: 370 с состоянием
`MATCH_ENDED` и итоговым счётом, один с `PERMANENT_BET_SUSPEND`. Последний не
выдаётся за завершённый результат. Реальный DB smoke сохранил 371 event,
742 event participant, 370 result и один parser run. Консервативная нормализация
дала 38 игроков, 16 команд и 0 сомнительных склеек. Доступная дневная история
начинается 2026-06-17; более ранние проверенные даты возвращали пустой массив.

### Почему это не целевой FC26 H2H

Пул официального SIS eSoccer содержит игроков `COSMOS`, `ALIBI`, `EXILE` и т. п.
Текущий Fonbet `FC 26. H2H LIGA-1/2/3/4` содержит `ARTEKUZ`, `JEKINHO`,
`BORISLOVEOG`, `SYNTHAYVY` и другие имена. Пересечения и совпадающих событий при
ручной проверке нет. Поэтому source code — `sis_h2h_esoccer`, family —
`h2h_ggl`; он не называется `h2h_ef` и не используется как история FC26 H2H.

## FC26 H2H Liga — источник пока не подтверждён

Поисковая выдача `cyberfifa.ru` содержит точные пары и исторические результаты
нужного пула игроков. Повторный прямой HTTP GET через текущий маршрут вернул 200,
но сама HTML-страница дважды явно сообщает, что это демо-страница и её данные
«не имеют ничего общего с реальной статистикой игрока». Фильтры полного набора
отключены без подписки.

Поэтому поисковые snippets и публичная demo-таблица нельзя использовать ни как
ground truth, ни как backfill. `CyberFifaProvider` намеренно не создан. Доступ к
платной версии сам по себе также не будет считаться разрешением на автоматизированный
сбор без проверки условий сервиса. В проекте нет синтетических матчей FC26 H2H Liga.

Официальная results-страница Fonbet показывает такие матчи поисковому индексу, но
прямой HTTP-доступ сейчас закрыт anti-bot защитой, а в подтверждённом line snapshot
нет terminal status. Live-счёт не повышается до финального результата по факту
исчезновения события. Подробное наблюдение находится в
`research/fonbet_result_status_evidence.json`.
