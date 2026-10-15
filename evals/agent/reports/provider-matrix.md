# Диагностика provider

Все вопросы и инструменты синтетические; инструменты не выполняются.

| Tools | Reasoning | Route | Повтор | HTTP | Время, с | ₽ | Оценка | Ошибка |
|---|---|---|---:|---:|---:|---:|---|---|
| none | default | auto | 1 | 200 | 4.927 | 0.01729785 | False | — |
| none | default | auto | 2 | timeout | 60.009 | 0.1542849312 | True | — |
| none | disabled | auto | 1 | 200 | 3.886 | 0.00096387 | False | — |
| none | disabled | auto | 2 | 200 | 4.022 | 0.00096387 | False | — |
| simple | default | auto | 1 | 200 | 19.426 | 0.00439095 | False | — |
| simple | default | auto | 2 | 200 | 7.369 | 0.00439095 | False | — |
| simple | disabled | auto | 1 | 200 | 4.616 | 0.00363057 | False | — |
| simple | disabled | auto | 2 | 200 | 3.561 | 0.00143723 | False | — |
| student | default | auto | 1 | 200 | 5.836 | 0.01415814 | False | — |
| student | default | auto | 2 | 200 | 3.587 | 0.01402963 | False | — |
| student | disabled | auto | 1 | 200 | 4.189 | 0.0134406 | False | — |
| student | disabled | auto | 2 | 200 | 5.677 | 0.0134406 | False | — |
| admin | default | auto | 1 | 200 | 4.449 | 0.03284647 | False | — |
| admin | default | auto | 2 | 200 | 4.482 | 0.02349481 | False | — |
| admin | disabled | auto | 1 | 200 | 3.397 | 0.03148634 | False | — |
| admin | disabled | auto | 2 | 200 | 4.372 | 0.00735966 | False | — |
| none | default | only_cheapest | 1 | 200 | 6.585 | 0.00416605 | False | — |
| none | default | only_cheapest | 2 | 200 | 4.657 | 0.00446592 | False | — |
| none | disabled | only_cheapest | 1 | 200 | 5.642 | 0.00096387 | False | — |
| none | disabled | only_cheapest | 2 | 200 | 4.177 | 0.00152077 | False | — |
| simple | default | only_cheapest | 1 | 400 | 2.021 | 0.1605555376 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "52486032-6eba-478c-8683-660b40cf68ca"}, "trace_id": "52486032-6eba-478c-8683-660b40cf68ca"} |
| simple | default | only_cheapest | 2 | 400 | 1.977 | 0.1605555376 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "4c690211-3428-4b85-a57f-45e2af75ab34"}, "trace_id": "4c690211-3428-4b85-a57f-45e2af75ab34"} |
| simple | disabled | only_cheapest | 1 | 400 | 2.806 | 0.1605555376 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "e77da4cf-9728-4213-b4ff-194face708e9"}, "trace_id": "e77da4cf-9728-4213-b4ff-194face708e9"} |
| simple | disabled | only_cheapest | 2 | 400 | 3.008 | 0.1605555376 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "b1b9219b-f942-4681-9738-1a172d016a61"}, "trace_id": "b1b9219b-f942-4681-9738-1a172d016a61"} |
| student | default | only_cheapest | 1 | 400 | 3.169 | 0.3276487552 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "e7fa4a69-6ae0-4e3c-88f8-3544de6b7fd6"}, "trace_id": "e7fa4a69-6ae0-4e3c-88f8-3544de6b7fd6"} |
| student | default | only_cheapest | 2 | 400 | 1.846 | 0.3276487552 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "152e8b32-26c5-4eda-9d3e-51a20e048b34"}, "trace_id": "152e8b32-26c5-4eda-9d3e-51a20e048b34"} |
| student | disabled | only_cheapest | 1 | 400 | 4.715 | 0.3276487552 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "1be0a30f-e06b-4d1e-818d-34488d6ba77a"}, "trace_id": "1be0a30f-e06b-4d1e-818d-34488d6ba77a"} |
| student | disabled | only_cheapest | 2 | 400 | 2.509 | 0.3276487552 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "fd80ba19-1d51-4d2d-a434-3a5d926f7915"}, "trace_id": "fd80ba19-1d51-4d2d-a434-3a5d926f7915"} |
| admin | default | only_cheapest | 1 | 400 | 3.647 | 0.5959938232 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "fb6d9e66-7796-4889-ad9e-e2c073607b91"}, "trace_id": "fb6d9e66-7796-4889-ad9e-e2c073607b91"} |
| admin | default | only_cheapest | 2 | 400 | 1.991 | 0.5959938232 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "95d66c45-5478-4fe3-baca-6e4d1711ca6a"}, "trace_id": "95d66c45-5478-4fe3-baca-6e4d1711ca6a"} |
| admin | disabled | only_cheapest | 1 | 400 | 5.113 | 0.5959938232 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "af971b43-6c06-4969-a26a-525ba668cf90"}, "trace_id": "af971b43-6c06-4969-a26a-525ba668cf90"} |
| admin | disabled | only_cheapest | 2 | 400 | 3.309 | 0.5959938232 | True | {"error": {"code": "BAD_REQUEST", "message": "Некорректный запрос.", "trace_id": "0ef7cef2-1210-428d-a17f-d1e6c23b1166"}, "trace_id": "0ef7cef2-1210-428d-a17f-d1e6c23b1166"} |
| simple | default | only_cheapest | 1 | 200 | 5.918 | 0.00439095 | False | — |
| simple | default | only_cheapest | 2 | 200 | 4.621 | 0.00219762 | False | — |
| simple | disabled | only_cheapest | 1 | 200 | 3.887 | 0.00363057 | False | — |
| simple | disabled | only_cheapest | 2 | 200 | 4.29 | 0.00143723 | False | — |
| student | default | only_cheapest | 1 | 200 | 5.132 | 0.00525629 | False | — |
| student | default | only_cheapest | 2 | 200 | 3.949 | 0.01402963 | False | — |
| student | disabled | only_cheapest | 1 | 200 | 4.727 | 0.0134406 | False | — |
| student | disabled | only_cheapest | 2 | 200 | 3.327 | 0.00466726 | False | — |
| admin | default | only_cheapest | 1 | 200 | 3.869 | 0.02334487 | False | — |
| admin | default | only_cheapest | 2 | 200 | 3.154 | 0.03237524 | False | — |
| admin | disabled | only_cheapest | 1 | 200 | 3.59 | 0.00735966 | False | — |
| admin | disabled | only_cheapest | 2 | 200 | 4.01 | 0.03148634 | False | — |
| simple | default | only_native | 1 | 200 | 2.992 | 0.00835352 | False | — |
| simple | default | only_native | 2 | 200 | 3.705 | 0.00675707 | False | — |
| simple | disabled | only_native | 1 | 200 | 3.038 | 0.00683632 | False | — |
| simple | disabled | only_native | 2 | 200 | 4.945 | 0.00459729 | False | — |
| student | default | only_native | 1 | 200 | 4.502 | 0.02563174 | False | — |
| student | default | only_native | 2 | 200 | 3.3 | 0.00629156 | False | — |
| student | disabled | only_native | 1 | 200 | 4.65 | 0.02318637 | False | — |
| student | disabled | only_native | 2 | 200 | 4.69 | 0.00527414 | False | — |
| admin | default | only_native | 1 | 200 | 3.709 | 0.03362898 | False | — |
| admin | default | only_native | 2 | 200 | 3.568 | 0.00904536 | False | — |
| admin | disabled | only_native | 1 | 200 | 3.577 | 0.03758941 | False | — |
| admin | disabled | only_native | 2 | 200 | 4.192 | 0.00624301 | False | — |
| none | default | order_cheapest_native | 1 | 200 | 7.103 | 0.00412321 | False | — |
| none | default | order_cheapest_native | 2 | 200 | 5.951 | 0.00517276 | False | — |
| none | disabled | order_cheapest_native | 1 | 200 | 3.695 | 0.00154219 | False | — |
| none | disabled | order_cheapest_native | 2 | 200 | 3.916 | 0.00154219 | False | — |
| simple | default | order_cheapest_native | 1 | 200 | 4.128 | 0.00219762 | False | — |
| simple | default | order_cheapest_native | 2 | 200 | 3.763 | 0.00439095 | False | — |
| simple | disabled | order_cheapest_native | 1 | 200 | 7.145 | 0.00143723 | False | — |
| simple | disabled | order_cheapest_native | 2 | 200 | 3.911 | 0.00143723 | False | — |
| student | default | order_cheapest_native | 1 | 200 | 20.429 | 0.00525629 | False | — |
| student | default | order_cheapest_native | 2 | 200 | 7.207 | 0.00525629 | False | — |
| student | disabled | order_cheapest_native | 1 | 200 | 3.684 | 0.00466726 | False | — |
| student | disabled | order_cheapest_native | 2 | 200 | 4.844 | 0.0134406 | False | — |
| admin | default | order_cheapest_native | 1 | 200 | 3.599 | 0.03211821 | False | — |
| admin | default | order_cheapest_native | 2 | 200 | 5.869 | 0.02362332 | False | — |
| admin | disabled | order_cheapest_native | 1 | 200 | 8.076 | 0.022713 | False | — |
| admin | disabled | order_cheapest_native | 2 | 200 | 3.512 | 0.00735966 | False | — |
| none | default | only_native | 1 | 200 | 4.196 | 0.00858556 | False | — |
| none | default | only_native | 2 | 200 | 5.884 | 0.01601091 | False | — |
| none | disabled | only_native | 1 | 200 | 3.44 | 0.00242752 | False | — |
| none | disabled | only_native | 2 | 200 | 3.018 | 0.00242752 | False | — |


## Вывод по совместимости

- Проверка 13.10.2026, модель `deepseek/deepseek-v4.1-flash`, вопрос «Покажи мой профиль.».
- С `require_parameters=true` и `only=["decart/fp4"]` запросы с tools и
  `parallel_tool_calls=false`: 12/12 HTTP 400. Без этого поля, при тех же схемах:
  12/12 HTTP 200 с вызовом `get_my_profile` (Student/Admin/simple, default/none).
- `deepseek` и порядок `["decart/fp4","deepseek"]` без неподдерживаемого поля:
  по 16/16 HTTP 200 (включая вариант без tools).
- Автомаршрутизация: 15/16 HTTP 200 и один таймаут; фиксированная маршрутизация
  дешевле и показала более устойчивый результат в этой небольшой выборке.
- Причина отказов при `require_parameters`: неподдерживаемый `parallel_tool_calls`.
  `$defs` и strict остаются в тех же схемах и успешно принимаются после удаления
  одного поля. Исправление использует штатный `OpenAIModelProfile` Pydantic AI.
- Исходный полный prompt после исправления прошёл; двухшаговый smoke тоже прошёл
  HTTP с usage. Counter в метрике заменён сравнением множеств, поскольку кейсы
  задают множество ожидаемых имён tools; дубли правильного чтения учитываются
  через расход и число запросов, а не как выбор неправильного инструмента.
- Provider не возвращается в проверенных успешных ответах: при оценке неизвестной
  цены используется максимальный тариф каталога, даже при ограничении `only`.

## Тарифы (₽ за 1M токенов, каталог проверен 13.10.2026)

| Provider | Вход | Выход | Кэш |
|---|---:|---:|---:|
| decart/fp4 | 10,70964 | 21,41928 | 2,141928 |
| deepseek | 17,8494 | 71,3976 | 0,356988 |
| Неизвестен: верхняя оценка | 92,2148 | 285,5904 | как обычный вход |

Выбран `decart/fp4`: оба проверенных исполнителя стабильно вызывают нужный tool,
а каталог указывает меньшие входную и выходную цены. Выбор на основе минимальной
диагностики; полная оценка качества должна быть подтверждена baseline.
