# FinCrime Lab: resumen en español

Detección de fraude, prevención de lavado de dinero (PLD), experimentación y modelado de pérdidas por fraude
sobre un **neobanco mexicano simulado** ("Banco Sintético"). Todo está construido con SQL, Python y estadística.

**Informe público con exploradores interactivos (en inglés):** <https://deadmigue55.github.io/fincrime-lab/>
· Documentación completa en inglés: [README](../README.md).

> Todo es **sintético**. No hay datos de ningún banco, cliente o transacción reales, y ningún resultado describe a una
> institución real. Los únicos datos reales son los puntos de referencia públicos usados como verificación externa (IEEE-CIS está cargado pero aún sin resultados de modelo).

## Por qué un banco simulado

Los datos bancarios reales son privados, y en datos reales nunca se sabe qué fraude se dejó pasar. En el simulador el
fraude y el lavado se **siembran con verdad conocida** (toma de cuenta, prueba de tarjetas, cuentas mula, pitufeo,
dispersión y concentración), así que cada método se evalúa contra la respuesta correcta. El simulador tiene tres
capas (verdad latente, políticas del banco y lo que el banco observa), por lo que también guarda los contrafactuales
y permite evaluar pruebas A/B de reglas antifraude.

Se calibra con agregados públicos (censo 2020 por estado, ENIF 2024 de INEGI y CNBV, mezcla de montos del SPEI);
todo lo demás es un supuesto con nombre en [docs/assumptions.md](assumptions.md). Los identificadores (CURP, CLABE) tienen
el formato correcto pero dígitos verificadores inválidos a propósito.

## Qué contiene

| Módulo | Qué responde |
|---|---|
| Plataforma de datos | Cargas diarias incrementales, contratos de calidad, Parquet + DuckDB, prueba de escala hasta 180 millones de filas |
| Fraude | Variables "punto en el tiempo" sin fuga de información, validación por tiempo, presupuesto fijo de revisiones |
| Pérdidas (estilo IBNR) | Maduración de contracargos, modelo frecuencia-severidad y capital al 99.5 % con intervalos |
| Experimentos | Pruebas A/B de reglas: potencia, pruebas secuenciales, CUPED, sesgo de selección |
| PLD | Reglas contra árboles contra variables de grafo, con carga de trabajo de investigadores |
| Gobierno de modelos | Fichas de modelo, informe de validación con 32 hallazgos y plan de monitoreo |

## Resultados principales (y su advertencia)

- **Fraude.** Con la misma carga de revisión que las reglas del banco (0.54 % de los intentos), el modelo encuentra
  **99.3 %** de los eventos de fraude contra **40.4 %** de las reglas. En **pesos** la diferencia es menor (97.1 % contra
  90.2 %), porque las reglas ya detectan las tomas de cuenta grandes. *Este 99 % es un resultado de validez del proceso,
  no de desempeño real:* las variables imitan los mecanismos del simulador. La verificación externa es el conjunto público
  BAF: 54.7 % del fraude con 5 % de falsos positivos.
- **Costo.** Con costos unitarios supuestos (MXN 15 por revisión, MXN 40 por cliente legítimo molestado), el modelo cuesta
  MXN 2.35 millones menos que las reglas en el trimestre de prueba. Con costos del doble, las reglas cuestan más que no hacer nada.
- **Pérdidas y capital.** Pérdida anual esperada de MXN 9.0 millones y capital de MXN 2.1 millones al 99.5 %
  (intervalo 1.1 a 3.8). En 200 bancos simulados, el intervalo del 90 % cubrió la pérdida real el 92 % de las veces.
  El método elegido de antemano resultó el peor calibrado y se reporta así.
- **Experimentos.** Revisar una prueba A/B cada semana declara un ganador falso en 28 % de las pruebas A/A; los límites de
  gasto de alfa lo mantienen entre 3 % y 5 %. La pérdida por fraude tiene poca potencia con 100,000 clientes por brazo.
- **PLD.** Cuatro reglas juntas encuentran los 219 episodios sembrados con unos 15 casos por semana; esa comparación
  se satura y se reporta como tal. Con presupuestos más estrictos los árboles superan a las reglas, pero se desploman
  cuando solo se ve la mitad de las redes de mulas.

## Límites

Datos sintéticos (las tasas base y el comportamiento de los atacantes son supuestos); las reglas y el simulador
comparten autor; el informe de validación es una **autocrítica**, no una validación independiente; los umbrales de
monitoreo son propuestas sin calibrar. La lista completa está en [docs/governance/](governance/README.md).

## Cómo ejecutarlo

```bash
uv sync
uv run fincrime doctor
uv run fincrime bank build --customers 20000
uv run pytest
```

Autoría: Miguel Vilchis, actuario. Hecho con asistencia de IA; las decisiones de diseño, los protocolos de evaluación
(fijados antes de ver los resultados) y la revisión de cada paso son parte del repositorio.
