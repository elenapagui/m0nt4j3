# CapCut: preparar el proyecto, moldes, color y problemas

## Preparar el proyecto (cada vídeo, 1 minuto)

1. Abre CapCut (versión de ordenador; la gratuita vale) → **Crear proyecto**.
2. **Importa** tu vídeo y arrástralo a la línea de tiempo. Déjalo entero, sin cortar.
3. Ponle un nombre fácil al proyecto (p. ej. «Opinión IA»).
4. Opcional: si quieres el color de la LUT de `montaje color` en todo el vídeo, aplícala ahora
   al clip (ver «Color» más abajo): se copiará a cada corte.
5. **Cierra CapCut del todo** (Cmd+Q). Si está abierto, CapCut sobrescribe los cambios al guardar.

Guarda tus vídeos fuera de Escritorio, Documentos y Descargas (por ejemplo en `Películas`): macOS
pide permisos especiales para esas carpetas y CapCut puede pedir «volver a vincular» los archivos.

## Moldes de texto: tu estilo de subtítulos y títulos (una vez)

La herramienta no inventa el diseño de los textos: lo copia de textos de ejemplo que hagas tú en
CapCut, con la fuente, colores, borde, sombra, posición y animaciones que quieras.

1. Crea un proyecto llamado **«Plantilla montaje»** (puede estar vacío o con cualquier vídeo).
2. Añade tres textos (Texto → Texto predeterminado) y escribe exactamente:
   - `SUBTITULO` → el estilo de los subtítulos. **Truco:** si pintas parte de la palabra de otro color
     (p. ej. «SUBTI» en blanco y «TULO» en amarillo), ese segundo color será el de las palabras resaltadas.
   - `TITULO` → los títulos de capítulo.
   - `CLAVE` → las frases clave que aparecen en pantalla.
3. Colócalos donde quieras que salgan y, si te gusta, ponles animación de entrada/salida.
4. Cierra CapCut.

Se usarán en todos tus montajes. Si un proyecto concreto lleva sus propios textos `SUBTITULO`,
`TITULO` o `CLAVE`, esos tienen prioridad (y se retiran de la línea de tiempo al montar).
Sin moldes se usa un diseño sencillo según el estilo (`montaje/estilos/*.json`).

## Color

`montaje color` produce tres cosas:

| Qué | Cómo llega a CapCut |
|---|---|
| Brillo, contraste y saturación | Automático: se escriben en cada corte (los verás como fotogramas clave del panel *Ajustar*). |
| `color.cube` (balance de blancos + look, o igualar a una referencia) | Tú la cargas una vez: selecciona el clip → **Ajustar** → **LUT** → importar el archivo `.cube`. |
| `color_completo.cube` | Todo junto, por si prefieres aplicar solo la LUT (`montaje color … --modo lut`). |

Dónde aplicar la LUT:
- **Antes de montar**, sobre el clip entero: cada corte la hereda.
- **Después de montar**, con una capa de ajuste que cubra todo el vídeo. Las capas de ajuste y los
  filtros que añadas sobre todo el vídeo se conservan si vuelves a montar.

Los menús de CapCut cambian entre versiones y algunas funciones (como importar LUT) pueden depender
de tu versión o plan. Si no la encuentras o no puedes usarla: `montaje color PLAN --hornear` crea un
vídeo nuevo con todo el color aplicado. Importa ese archivo en el proyecto en vez del original
(transcripción y plan siguen valiendo, porque los tiempos son idénticos).

## Qué hace `montar` con tu proyecto

- Hace **copia de seguridad** antes de escribir, en `~/Movies/Montaje/copias/<proyecto>/`
  (`original/` es el proyecto tal y como estaba antes del primer montaje).
- Corta tu clip en tantos trozos como haga falta (cada uno conserva el encuadre y los ajustes del clip
  original), añade pistas de subtítulos, títulos, B-roll, efectos y música.
- Puedes volver a montar tras cambiar el plan: siempre parte del original.
- Si has retocado el montaje a mano en CapCut, se niega a pisarlo salvo con `--forzar`.
- `montaje deshacer --proyecto "Nombre"` devuelve el proyecto al estado original.

## Problemas frecuentes

| Síntoma | Solución |
|---|---|
| «CapCut está abierto» | Cierra CapCut con Cmd+Q (no basta cerrar la ventana) y repite. |
| No encuentra el proyecto | `montaje proyectos` muestra los nombres. Si tus borradores están en otra carpeta (CapCut → Ajustes → Borradores), indica la ruta con `export CAPCUT_DRAFTS_DIR="…"`. |
| «En el proyecto no está tu vídeo» | El proyecto debe contener el mismo archivo que transcribiste (o uno con la misma duración, como el horneado). |
| CapCut muestra el proyecto vacío, dañado o pide vincular medios | `montaje deshacer`, actualiza esta herramienta y avisa de tu versión de CapCut (`montaje doctor`). Mientras tanto: `montaje previsualizar`, `montaje srt` (CapCut importa .srt) y `--hornear`. |
| Los cambios no aparecen | CapCut a veces necesita cerrarse y abrirse otra vez para releer la carpeta. |

### Sobre versiones de CapCut

CapCut no tiene una API pública: esta herramienta escribe directamente el formato de proyecto de la
app, que ByteDance no documenta y cambia entre versiones. Para minimizar riesgos:

- Clona las estructuras de **tu propio** proyecto (creado por tu versión de CapCut), en lugar de
  generarlas desde cero.
- Escribe todos los «espejos» del proyecto (`draft_info.json`, `draft_content.json`,
  `Timelines/<id>/…`) para que dé igual cuál lea tu versión.
- Valida el resultado antes de escribir y guarda copias.

Aun así, una actualización de CapCut puede romper la compatibilidad. Si te funciona bien, considera
desactivar las actualizaciones automáticas de CapCut hasta comprobar que una versión nueva también va.
Ten en cuenta también que los términos de uso de CapCut restringen la ingeniería inversa y la
automatización; úsalo bajo tu propia responsabilidad.
