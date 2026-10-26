# montaje

**Claude edita tus vídeos largos a cámara (opinión, vlogs, charlas) y te los deja montados en CapCut.**

Grabas, le dices a Claude «edita este vídeo» y al abrir CapCut encuentras un proyecto normal y
100 % editable, con cada cosa en su capa:

```
Tu vídeo ➜ Transcripción ➜ Limpieza (silencios, muletillas, tomas repetidas) ➜ Gancho y capítulos
        ➜ Subtítulos con palabras clave ➜ Títulos y zooms ➜ Sonidos, música y B-roll
        ➜ Corrección de color ➜ Proyecto en CapCut
```

Pensado para vídeos de 5 a 60 minutos en macOS con CapCut de escritorio (la versión gratuita vale).

## Cómo es posible (sin magia)

CapCut no tiene una API, pero guarda cada proyecto como archivos JSON en tu Mac
(`~/Movies/CapCut/User Data/Projects/com.lveditor.draft/`). Ese archivo describe la línea de tiempo:
qué clip va en qué momento, qué textos, qué sonidos, qué ajustes de color… Si un programa escribe ese
archivo correctamente con CapCut cerrado, al abrir CapCut ves el montaje hecho. Así funcionan este
proyecto y las herramientas parecidas (capcut-mcp, SmartCut, La Máquina de Contenido…).

El reparto del trabajo:

| Quién | Qué hace |
|---|---|
| **Whisper** (en tu Mac, sin subir nada) | Transcribe con el tiempo exacto de cada palabra. |
| **ffmpeg** | Mide silencios, analiza el color, genera vista previa y sonidos. |
| **`montaje`** (este repo) | Propone cortes, calcula la línea de tiempo, escribe el proyecto de CapCut con copia de seguridad. |
| **Claude** | El criterio editorial: lee la transcripción, decide qué tomas se quedan, elige el gancho, los capítulos, los títulos y los zooms, revisa el color mirando la imagen antes/después. |

## Qué hace

- **Pre-corte**: quita silencios, muletillas (eh, em, mmm…), tomas repetidas y comentarios de toma
  («perdón, otra vez»), dejando el aire justo para que no suene robótico.
- **Gancho**: puede abrir el vídeo con la frase más potente antes del saludo.
- **Capítulos**: títulos en pantalla + texto listo para la descripción de YouTube.
- **Subtítulos** con palabras clave y números resaltados (sin cortes feos tipo «…el 80% en»).
- **Títulos de frases clave**, **zoom alterno** entre cortes (oculta los saltos) y **zooms de énfasis**.
- **Efectos de sonido** (incluye unos básicos libres de derechos), **música** con fundidos y **B-roll**.
- **Color**: corrige exposición, contraste, saturación y balance de blancos; looks (natural, cálido,
  frío, cine, suave, vivo) o **igualar el color a un vídeo que te guste**; imagen antes/después.
- **Aprender de referencias**: analiza el ritmo (cortes por minuto) y el color de vídeos que te gustan.
- Tus **moldes de texto** de CapCut (fuente, colores, animaciones) se usan para subtítulos y títulos.
- Copias de seguridad y `deshacer`; vista previa y .srt como plan B.

## Requisitos

- Mac (mejor con chip Apple M1 o posterior: transcribe mucho más rápido).
- CapCut de escritorio.
- Claude Pro o superior para usar **Claude Code** (desde la app de Claude o la Terminal).
- ~5 GB libres (modelo de transcripción + entorno).

## Instalación (una vez, ~15-30 min)

1. Descarga este repositorio (botón verde **Code → Download ZIP**, o `git clone`) y descomprímelo,
   por ejemplo en `~/montaje`.
2. Abre **Terminal** en esa carpeta y ejecuta:
   ```bash
   ./instalar.sh
   ```
   Instala ffmpeg y Python con Homebrew (te dirá cómo instalar Homebrew si no lo tienes), crea el
   entorno y comprueba que todo funciona. También puedes pedírselo a Claude Code: «instala esto».
3. Una vez: crea en CapCut el proyecto **«Plantilla montaje»** con tus textos de ejemplo
   `SUBTITULO`, `TITULO` y `CLAVE` (ver [docs/capcut.md](docs/capcut.md)). Es opcional pero es lo
   que hace que el resultado tenga *tu* estilo.

## Uso con Claude

1. En CapCut: crea un proyecto, importa tu vídeo, arrástralo a la línea de tiempo y **cierra CapCut**.
2. Abre esta carpeta con Claude Code y pídelo con tus palabras, por ejemplo:

   > Edita `~/Movies/grabaciones/ia-opinion.mov`, estilo opinión. El proyecto de CapCut se llama
   > «IA opinión». Quiero un gancho fuerte al principio y color cálido.

Claude transcribe, te propone los cortes, capítulos y títulos, corrige el color (enseñándote el
antes/después), monta el proyecto y te dice qué revisar. Puedes pedir cambios («deja la anécdota
del minuto 12», «menos títulos», «más ritmo») y volver a montar las veces que quieras.

## Uso a mano (sin Claude)

```bash
./montaje.sh preparar ~/Movies/video.mov --estilo opinion   # transcribe y propone (trabajos/video/)
open trabajos/video/revision.md                             # revisa la propuesta
./montaje.sh frases trabajos/video/plan.json --quitar F012,F030-F033
./montaje.sh color trabajos/video/plan.json --look calido   # mira color_antes_despues.jpg
./montaje.sh montar trabajos/video/plan.json --proyecto "Mi vídeo"
./montaje.sh capitulos trabajos/video/plan.json             # para la descripción de YouTube
./montaje.sh deshacer --proyecto "Mi vídeo"                 # si algo no te gusta
```

Todas las órdenes: `./montaje.sh --help`. Formato del plan: [docs/plan.md](docs/plan.md).

## Estilos y referencias

Incluye tres estilos (`./montaje.sh estilos`):

- **opinion**: ritmo ágil, zoom alterno, subtítulos con resaltados, títulos de capítulo y frases clave.
- **vlog**: ritmo natural (respeta más las pausas), zoom suave, look cálido.
- **sobrio**: solo limpieza de silencios y tomas; ideal para seguir editando a mano.

Para que se parezca a vídeos que te gustan, pásale a Claude el archivo de vídeo de referencia
(descargado o grabado de pantalla) y te creará un estilo propio midiendo su ritmo y su color
(`./montaje.sh referencia`), y puede igualar tu color al suyo (`./montaje.sh color --referencia`).

## Límites y avisos

- CapCut no tiene una API oficial. Este programa escribe el formato interno de los proyectos, que
  cambia entre versiones de CapCut: está diseñado para ser prudente (copia las estructuras de tu propio
  proyecto, valida, hace copias), pero **una actualización de CapCut puede romperlo**. Si pasa:
  `deshacer` y avisa con la versión (`./montaje.sh doctor`). Más detalles en [docs/capcut.md](docs/capcut.md).
- Los términos de uso de CapCut restringen la ingeniería inversa y la automatización: úsalo bajo tu
  responsabilidad.
- Pensado para vídeo hablado: sin voz no hay transcripción y no hay nada que cortar.
- La música y los efectos de librería de CapCut no se pueden añadir automáticamente (sus ids son
  internos): usa tus archivos (carpeta `sonidos/`) o añádelos luego en CapCut.

## Desarrollo

```bash
.venv/bin/pytest            # tests (usan un proyecto real de CapCut 8.7 como fixture)
```

Créditos: el conocimiento del formato de CapCut se apoya en el trabajo abierto de
[capcut-cli](https://github.com/renezander030/capcut-cli) (MIT; el borrador de ejemplo de
`tests/fixtures/capcut-8.7` procede de su repositorio), [pyCapCut / pyJianYingDraft](https://github.com/GuanYixuan/pyCapCut),
[capcut-mcp](https://github.com/JmsLdrn/capcut-mcp) y [SmartCut](https://github.com/mrbuslov/capcut-ai-editor).
No está afiliado a CapCut ni a ByteDance.
