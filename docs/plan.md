# Formato de `plan.json`

`montaje preparar` crea el plan con la propuesta automática. Claude (o tú) lo ajusta y después
`montaje montar` lo convierte en un proyecto de CapCut.

**Todos los tiempos son del vídeo ORIGINAL.** Para situar algo se usa el id de una frase
(`"phrase": "F012"`) o segundos (`"at": 754.2`). La herramienta calcula dónde cae en el montaje final,
así que no hay que recalcular nada al cortar o descortar frases.

Las rutas de archivos pueden ser absolutas o relativas a la carpeta del plan.

```jsonc
{
  "version": 1,
  "source": "/Users/tu/Movies/opinion.mov",   // vídeo original (debe estar importado en CapCut)
  "transcript": "transcripcion.json",
  "style": "opinion",                           // opinion | vlog | sobrio | ruta a un .json propio

  // Frases de la transcripción. keep=false → se corta. "w" = rango de índices de palabra [inicio, fin)
  "phrases": [
    {"id": "F001", "w": [0, 6], "start": 0.8, "end": 3.9, "text": "Hola a todos…", "keep": true},
    {"id": "F002", "w": [6, 15], "start": 4.2, "end": 8.1, "text": "Hoy quiero…", "keep": false,
     "reason": "toma repetida (mejor versión en F003)"}
  ],
  "cut_words": [27, 74],                 // índices de palabras sueltas a cortar (muletillas)
  "remove_ranges": [[612.4, 615.0]],     // tramos a quitar a mano (una tos, un ruido…)

  "hook": {"phrases": ["F088", "F089"]}, // gancho: se copian al principio del vídeo

  "chapters": [                          // título grande + efecto de sonido + capítulos de YouTube
    {"phrase": "F010", "title": "1. El problema"},
    {"at": 512.0, "title": "2. Mi experiencia", "duration": 3}
  ],
  "titles": [                            // textos en pantalla mientras se dice la frase
    {"phrase": "F034", "text": "El 80% fracasa", "kind": "keyword"},   // keyword | title
    {"phrase": "F120", "text": "Lo que nadie te cuenta", "kind": "title", "duration": 3, "sfx": false}
  ],
  "highlights": ["libertad", "inteligencia artificial"],  // se resaltan en los subtítulos

  "zooms": [
    {"phrase": "F045", "kind": "push", "scale": 1.12},    // acercamiento lento durante la frase
    {"phrase": "F078", "kind": "punch", "scale": 1.25}    // salto de zoom
  ],
  "sfx": [
    {"phrase": "F078", "sound": "impacto", "volume": 0.5, "offset": -0.1}
    // sound: whoosh | pop | click | ding | impacto | nombre de un archivo de sonidos/ | ruta
  ],
  "overlays": [                          // B-roll / imágenes encima del vídeo
    {"phrase": "F050", "file": "broll/oficina.mp4", "from": 2.0},          // a pantalla completa
    {"at": 900.0, "duration": 4, "file": "captura.png", "mode": "pip", "scale": 0.4,
     "position": [0.55, 0.45]}                                             // ventana pequeña
  ],
  "music": {"file": "musica/fondo.mp3", "volume": 0.07, "fade_in": 2, "fade_out": 4},
  // opcional: "phrase"/"at" para empezar más tarde, "end" para acabar antes, "loop": false

  "color": {                             // lo rellena `montaje color`
    "mode": "auto",                      // auto | lut | ninguno | hornear
    "capcut": {"brightness": 0.08, "contrast": 0.1, "saturation": 0.05},  // -1…1 (≈ ×50 en CapCut)
    "lut": ".../color.cube", "lut_completa": ".../color_completo.cube", "look": "natural"
  },

  "overrides": {                         // retoques del estilo solo para este vídeo
    "cuts": {"min_pause": 0.5},
    "zoom": {"anchor": [0.0, 0.3], "scale": 1.1},
    "captions": {"enabled": true, "uppercase": true, "max_chars": 30}
  }
}
```

## Estilos

Los estilos están en `montaje/estilos/*.json`. Para crear uno, copia uno existente con otro nombre.

| Clave | Qué controla |
|---|---|
| `cuts.min_pause` | Pausa (s) a partir de la cual se corta. Más bajo = más ritmo. |
| `cuts.pad_before` / `pad_after` | Aire que se deja antes/después de hablar al cortar. |
| `zoom.mode` | `alternate` (alterna encuadre normal/cercano entre cortes) o `none`. |
| `zoom.scale` / `min_interval` | Cuánto se acerca y cada cuántos segundos puede cambiar. |
| `zoom.anchor` | Punto de la pantalla que queda fijo al hacer zoom (la cara). x,y de -1 a 1 (y hacia arriba). |
| `captions.*` | Subtítulos: activado, caracteres por línea, mayúsculas, puntuación, tamaño, colores, borde, posición. |
| `titles.*` / `keywords.*` | Títulos de capítulo y palabras clave: tamaño, color, posición, duración, sonido. |
| `sfx.volume`, `music.*` | Volumen de efectos y música, fundidos. |
| `color.look` / `strength` | Look de color por defecto e intensidad de la corrección. |

Si en CapCut usas moldes de texto (ver `docs/capcut.md`), su aspecto manda sobre `captions`, `titles`
y `keywords` (tamaño, fuente, colores, posición, animaciones).
