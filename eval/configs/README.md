# Конфигурации эксперимента

Файлы для `manage.py evaluate --config`. Каждый задаёт один набор условий,
чтобы сравнения были воспроизводимыми: меняется ровно один параметр.

| Файл | Что задаёт | С чем сравнивать |
|---|---|---|
| `base.json` | старшая модель, тип определяется автоматически | эталон для остальных |
| `hint.json` | тип берётся из эталона, классификация не вызывается | с `base` — цена режима «Авто» |
| `lite.json` | младшая модель | с `base` — разница в качестве и расходе |

`prompt_v2.json` появится вместе с промптом версии v2: сейчас в
`documents/services/prompts.py` есть только `v1`, и конфиг ссылался бы
на несуществующую версию.

## Команды

```bash
# базовая конфигурация на dev-части
python manage.py evaluate --split dev --config eval/configs/base.json \
    --out eval/results/dev-base

# тип из эталона
python manage.py evaluate --split dev --config eval/configs/hint.json \
    --out eval/results/dev-hint

# младшая модель
python manage.py evaluate --split dev --config eval/configs/lite.json \
    --out eval/results/dev-lite

# сравнение форматов: один документ прогоняется и как DOCX, и как PDF
python manage.py evaluate --split dev --config eval/configs/base.json \
    --format both --out eval/results/dev-formats
```

## Продолжение прерванного прогона

Результат каждого документа пишется в `<out>/progress.jsonl` сразу после
обработки. Если прогон оборвался, его продолжает та же команда с `--resume`:

```bash
python manage.py evaluate --split test --config eval/configs/base.json \
    --out eval/results/test-base --repeat 3

# ... через шесть часов связь оборвалась ...

python manage.py evaluate --split test --config eval/configs/base.json \
    --out eval/results/test-base --repeat 3 --resume
```

`--resume` требует `--out`: папка прогона должна быть фиксированной, иначе
продолжать будет нечего. Обработанные документы повторно не прогоняются,
их результаты и токены восстанавливаются из файла прогресса.
