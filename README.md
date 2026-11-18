# radar-rondas

Cada mañana lee los RSS de webs de noticias de financiación, detecta qué titulares anuncian una ronda, extrae **empresa, fase y cantidad**, se queda con las **post-seed** y te deja la lista del día.

## Qué genera

- `daily/AAAA-MM-DD.md`: tabla del día (empresa → artículo, ronda, cantidad, fuente, búsqueda de fundadores en LinkedIn) y, plegadas, las rondas seed o con datos incompletos.
- `data/companies.csv`: histórico acumulado. También sirve para no repetir empresas: una empresa ya listada en los últimos 90 días no vuelve a salir.
- Una **issue** en GitHub cada día que haya resultados. GitHub te la manda por email.

## Puesta en marcha (GitHub Actions, gratis)

1. Crea un repo (puede ser privado) y sube esta carpeta tal cual, incluida `.github/`.
2. En **Actions**, actívalas si te lo pide y lanza `radar-rondas` → **Run workflow** para probar.
3. En el repo, **Watch → All Activity**, para que las issues diarias te lleguen al correo.

Se ejecuta todos los días a las 07:17 (hora de Brasilia). Como hace un commit diario, GitHub no desactiva el cron por inactividad.

## Uso local

```bash
python3 radar_rondas.py --dry-run          # imprime la lista sin escribir nada
python3 radar_rondas.py --include-seed     # cuenta también Seed / Pre-Series A
python3 radar_rondas.py --min-usd 15       # umbral para rondas sin fase (US$ millones)
python3 -m unittest discover -s tests      # tests
```

Sin dependencias: solo la biblioteca estándar de Python 3.9+.

## Criterio "post-seed"

- Si el titular dice la fase: **Series A o posterior** (o Seed en adelante, con `--include-seed`).
- Si no la dice: cantidad ≥ **~US$8M**, convertida con tipos de cambio aproximados que solo sirven para este umbral.

## Limitaciones conocidas

- La extracción se basa en patrones sobre el titular, no en entender el texto. Un titular raro puede dar un nombre de empresa equivocado. El enlace al artículo siempre está ahí para comprobarlo.
- Se descartan las rondas cuyo sujeto parece un inversor (palabras como *Ventures*, *Capital* o *Partners*). Una startup que se llame "X Capital" se perdería.
- EU-Startups y FinSMEs a veces bloquean las peticiones automáticas. Si fallan, aparecen al final del informe y el resto sigue funcionando.
- Para añadir una fuente, agrega una línea en `FEEDS` dentro de `radar_rondas.py`. Casi todas las webs de WordPress tienen el feed en `/feed/`.
