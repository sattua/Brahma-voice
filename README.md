# Brahma

Una persona cuenta una historia. El programa la parte en páginas y dibuja la novela gráfica de forma que el mismo personaje siga siendo el mismo de viñeta en viñeta. Cada página se entrega como [OpenRaster](https://www.openraster.org/) (`.ora`): capas de verdad, editables en Krita, GIMP, MyPaint y Drawpile.

El dibujo sale de lo aprendido en `graphic-harness`. Allí una imagen de referencia manda más que un párrafo largo, y en esta máquina (RX 9070 XT de 16 GB, 32 GB de RAM) SDXL con el muestreador ya medido cabe y es repetible. Brahma usa esa misma idea para una historia, no para un objeto 3D.

## Consistencia

Hay tres cerraduras.

1. **Estilo.** Una sola frase (medio, línea, color, luz) y un solo muestreador. Checkpoint, pasos, CFG, sampler y scheduler son los mismos en todas las imágenes. Cambiar el sampler es cambiar de dibujante.
2. **Identidad.** Cada personaje tiene una hoja de modelo, un giro de 360 (frente, tres cuartos, perfil y espalda) y una hoja de poses (primeros planos del rostro arriba y hasta seis poses de acción abajo, elegidas con `novel_character` `poses`). Cada lugar tiene una placa. Se generan una vez, en PNG, y se pueden repintar. El peso es 0.85 para el personaje y 0.55 para el lugar.
3. **Plano.** El encuadre sale de una lista cerrada (`close`, `medium`, `wide`, `establishing`, …). Así cambia la cámara sin reescribir a la persona.

### Orden de una viñeta: lugar, bloqueo, personajes, filtro

Es una regla fija de toda la aplicación. Cada viñeta, de página o de secuencia, se arma en cuatro pasos y nunca de una vez:

1. **El lugar, vacío** (la placa): sin personas, sin criaturas y sin efectos. Mira solo la placa del lugar. La muestra de estilo no va: suele ser un retrato, y la placa copiaría su encuadre y sus partículas; el estilo viaja como texto.
2. **El bloqueo**, que la persona aprueba: dónde está cada personaje y qué tan alto se ve. Es la placa con un monigote por personaje, los pies en su sitio, numerados en el orden en que entran, la línea punteada del horizonte y la altura de cada uno como parte del cuadro (`alto 41%`). Un dibujo en papel se lee igual: cada monigote, de la cabeza a los pies, como fracción del recuadro que lo rodea. El agente mira la placa y fija el horizonte (`horizon`, de 0 a 1 desde arriba). Con él, `scale_off` avisa cuando un personaje de pie es demasiado grande o demasiado chico para el lugar donde tiene los pies. En una cámara quieta, las figuras de pie ya aceptadas sobre esa placa fijan una sola proporción de suelo (`ground_ratio`): el alto de una figura nueva es `(pies − horizonte) / proporción`, y quien se aparte más del 15% queda en `scale_off` aunque las figuras nuevas coincidan entre ellas. El primer panel, y una cámara que se mueve, usan la mediana de ese panel, porque todavía no hay suelo aceptado. Una viñeta de página no toma el suelo de otra. Ninguna figura se dibuja antes de que la persona diga que sí. Cambiar una caja, una pose, el horizonte o la placa pide el sí otra vez.
3. **Los personajes**, uno por uno, de atrás hacia adelante. Cada uno se dibuja solo, de cuerpo entero, sobre verde plano. La herramienta quita el verde, lo escala a su caja (`blocking`) con los pies en el borde de abajo y le pone una sombra suave en el suelo. Fuera de la figura y su sombra, el lugar queda igual, píxel por píxel. El modelo de imagen reencuadra lo que recibe, por eso no se le pide pintar dentro de una caja sobre el lugar.
4. **Un filtro**, solo si se pide: chispas, lluvia, niebla, brillo, rayos de luz. Se dibuja sobre la viñeta terminada en un archivo aparte (`-fx.png`), que en la página es su propia capa y nunca sirve de referencia.

Los efectos no entran en ninguna imagen base. Las herramientas los sacan de la apariencia, de los lugares y de las acciones, los guardan en `settings.json` `effects` y los dejan siempre en la negativa: partículas, chispas, motas que brillan, luciérnagas, bokeh, destellos de lente, rayos de luz, brillo, niebla, bruma, lluvia, nieve, humo y brasas. La luz de estudio de las hojas no llega a las escenas, y la de las escenas no llega a las hojas.

**Nunca dos poses iguales.** En una viñeta cada figura tiene su propia pose y su propio paso. Un `blocking` con dos poses iguales se rechaza. Si una figura repite la pose del panel anterior y el beat no dice que se quede quieta, sale el aviso `pose_repeated`. `novel_review` devuelve `duplicate_pose` cuando dos figuras se parecen demasiado, también reflejadas. Se vuelve a dibujar solo esa figura.

Qué imagen puede mirar una imagen nueva. Hay solo dos reglas:

- **Una viñeta de página**: su placa mira solo la placa del lugar. Cada figura mira la hoja de su personaje, la vista del giro que corresponde a cómo está de cara a la cámara (`facing`; si falta, `over-shoulder` es espalda y lo demás es frente) y su hoja de poses. Nunca mira otra viñeta de página: copiar el cuadro previo repite la pose y aun así deja que la cara se vaya.
- **Una secuencia** es una sola toma continua. Con cámara fija (`camera: static`) todos los paneles comparten una placa. Con cámara que se mueve, la placa n se dibuja desde la placa n-1. Cada figura mira la vista del giro, su hoja de poses y la misma figura en el panel n-1 (su recorte sobre verde), y solo cuando n-1 está aceptado.

La cláusula visual del personaje es la misma cadena en la hoja y en cada viñeta. Incluye `body`, el plan del cuerpo (por ejemplo, seis extremidades), y `marks`, lo que va en un solo lado. Una marca en el lado izquierdo de quien la lleva queda a la derecha del cuadro de frente y a la izquierda de espaldas. Las herramientas lo dicen por vista, para que la lámpara o la mano no cambien de lado. El diálogo se queda en el idioma de quien contó la historia; las descripciones que ve el modelo de imagen van en inglés.

Las negativas compartidas son texto, marcas de agua, bordes, caras repetidas y los efectos. Lo demás lo decide cada libro en `settings.negative`. Una palabra de la negativa que contradice el `art` o el `rendering` (por ejemplo, `photorealistic photo` en un libro fotográfico) se quita sola.

Un boceto con `id` es el dibujo de ese personaje. Viaja con su hoja, su giro, sus poses y sus viñetas, y nunca con la placa del lugar ni con la muestra de estilo, donde pintaría al personaje en un paisaje vacío. Un personaje con `visual: false`, como un narrador, no tiene hoja.

En una GPU de 16 GB entra **un** IP-Adapter. Un segundo adaptador de estilo pelea con el primero y se queda sin memoria. El estilo ya está dentro de la hoja, porque la hoja se generó con la cerradura de estilo. Las hojas son texto a imagen. Solo las viñetas condicionan en píxeles.

## Qué modelo

El guion y el dibujo no comparten modelo, y no corren a la vez. Es la misma disciplina que la cola de GPU del harness: una cosa usa la tarjeta.

**Guion: qwen3:8b** en Ollama (`http://127.0.0.1:11434/v1`), con el pensamiento apagado para que devuelva el JSON directo. Es el Qwen que ya está en esta máquina: cabe, sigue el esquema (reparto, lugares, páginas, planos) y no sostiene la GPU mientras se dibuja. Un 70B no cabe junto a esta tarjeta y 32 GB de RAM. El contrato es el JSON; otro modelo se cambia en `script.model`.

**Imagen: la herramienta de imagen de Cursor.** ComfyUI no está en esta máquina. El servidor no dibuja: devuelve un job con la descripción cerrada, el aspecto y `reference_image_paths`. El agente genera esa imagen y devuelve el archivo. La hoja del personaje es la referencia de cada viñeta suya; la placa, la de los planos vacíos.

Cuando haya un ComfyUI en el puerto 8188, `image.backend` pasa a `comfyui`. Ahí el dibujo es SDXL (30 pasos, CFG 6.5, `dpmpp_2m`, `karras`, lado largo 1024) y, con el paquete IP-Adapter, `workflow: "sdxl_ipadapter"` condiciona en la misma hoja. Un solo adaptador: en 16 GB un segundo se queda sin memoria.

## Ediciones baratas y caras

Igual que el harness elige el camino más barato:

| Qué cambió | Qué se vuelve a hacer |
| --- | --- |
| Un globo, una frase | `novel_assemble` con `dialogue`. No se redibuja. |
| Una parte de una imagen (una mano, una lámpara) | `novel_edit` con la región. Solo esa región se pega; el resto queda igual, píxel por píxel. |
| Una figura en una viñeta | Antes de aceptarla, su toma vuelve con `accept: false`; después, `novel_edit` en su caja. El lugar y las otras figuras no se tocan. |
| Dónde está cada figura | `novel_draw` con `blocking` (`{panel: {horizon, figures: [{character, box, pose}]}}`). La vista previa nueva espera el sí (`approve_blocking`); después las figuras se vuelven a poner y la placa se queda. |
| Un efecto (lluvia, chispas, niebla) | `novel_draw` con `panel` y `effects`, o `novel_sequence` `op filter`. Es un filtro aparte; la viñeta no se redibuja. |
| Una viñeta | `novel_draw` con `panel`, por ejemplo `p01_02`. Empieza otra vez por el lugar vacío. |
| Una hoja | `novel_refs` con `force` y `key`, por ejemplo `character:ana`. Las demás no se tocan. |
| La cara o el abrigo | `novel_character`, una hoja nueva y después las viñetas de ese personaje. |
| El estilo | Hoja de estilo, hojas y placas nuevas, y todas las viñetas. |

`pages/` se regenera. El original es `project.json` (texto) y los PNG de `panels/` y `refs/`. Si se va a retocar el `.ora` a mano y después se va a reensamblar, se guarda una copia.

## Tomas

Ninguna imagen se pierde. Cada archivo que vuelve de la herramienta de imagen se guarda en `takes/<clave>/NN.png`, con un `NN.json` que dice si fue aceptada, rechazada o reemplazada, y por qué. La imagen aceptada se copia a su lugar (`refs/…`, `panels/…`). Rechazar una toma es pasar `accept: false` y una nota: queda guardada en el libro y se puede abrir.

## Revisar antes de aceptar

`novel_review` corta una imagen en zonas con nombre (cabeza, manos, pies; o cielo, centro, suelo en una toma ancha) y, si se le da otra imagen, arma una tira lado a lado y dice cuánto cambió cada zona. Las zonas que se le pasan una vez se guardan junto a la imagen y se usan en la siguiente revisión. Se acepta lo que se vio en los recortes.

## Formato de página

Página de 6×9 pulgadas a 300 dpi (1800×2700). Cada viñeta es un grupo con dos capas, `art` y `lettering`, y una tercera, `filter`, sobre el arte cuando hay un filtro. Debajo, `frames` y `paper`. Los globos no se hornean en el dibujo. El PNG suelto de cada viñeta, a tamaño de generación, queda en `panels/` por si se quiere sustituir el arte y reensamblar.

Límites de producción, por tiempo de GPU: 8 páginas, 6 viñetas, 4 personajes, 4 lugares.

## Uso desde Cursor

El servidor MCP `brahma` ya está registrado. No hay comandos que lanzar. En el chat se cuenta la historia y el agente usa las herramientas: `novel_plan`, `novel_settings`, `novel_refs`, `novel_character`, `novel_draw`, `novel_sequence`, `novel_edit`, `novel_review`, `novel_assemble`. Cuando cambia el servidor, hay que recargar el MCP `brahma` en Cursor.

Una imagen adjunta puede ser referencia gráfica (`role: style`, `character` o `location`): se copia a `references/` y viaja en `reference_image_paths`. Si es un dibujo a mano (`role: sketch`), el chat lo mira y esa lectura (medio, paleta, trazo, notas) se escribe en `settings.json`. El archivo del dibujo sigue siendo referencia. No hay un modelo de visión local; si la lectura no viene en la llamada, la herramienta responde `needs_reading` y se completa con `novel_settings`.

En esta máquina el guion lo escribe **qwen3:8b** en Ollama, que es el Qwen que ya está instalado. Los píxeles los pide el agente a la herramienta de imagen de Cursor. Cada viñeta recibe la hoja del personaje en `reference_image_paths`; esa ruta es la cerradura de identidad. ComfyUI no está instalado, así que el flujo `sdxl_ipadapter` queda para cuando exista un servidor en el puerto 8188 (`image.backend = "comfyui"`).

```text
novel_plan → dibujar cada job de referencia → novel_refs con los archivos
→ novel_draw → placa vacía de cada viñeta → novel_draw con los archivos
→ vista previa del bloqueo → la persona dice que sí → novel_draw con approve_blocking
→ cada figura en su caja → novel_draw con los archivos (hasta que no queden jobs)
→ filtro, solo si se pide → novel_assemble
```

```text
workspace/<historia>/
  settings.json              # tipo de arte, paleta, línea, luz y las imágenes de referencia
  references/                # dibujos y referencias subidas
  project.json
  story.txt
  refs/style.png
  refs/characters/<id>.png
  refs/characters/<id>-360.png         # giro: frente, tres cuartos, perfil, espalda
  refs/characters/views/<id>-<vista>.png  # una columna del giro, recortada sola
  refs/characters/<id>-poses.png       # rostro de cerca y poses de acción
  refs/locations/<id>.png
  panels/p01_01-plate.png    # el lugar vacío
  panels/p01_01-blocking.png # monigotes, horizonte y alturas sobre la placa
  panels/p01_01-base.png     # la placa con las figuras que ya entraron
  panels/p01_01-base-<id>-cutout.png  # cada figura recortada del verde
  panels/p01_01.png          # la viñeta terminada, a resolución de generación
  panels/p01_01-fx.png       # el filtro, si se pidió
  panels/p01_01.json         # prompt, semilla, referencia y sampler usados
  panels/<secuencia>/sequence.json
  panels/<secuencia>/plate.png         # cámara fija; plate-01.png… si se mueve
  panels/<secuencia>/panel-01.png
  takes/<clave>/01.png       # cada intento, aceptado o no, con su nota
  edit/                      # máscaras de las ediciones por región
  review/                    # recortes y tiras de comparación
  pages/page-01.ora          # capas, para Krita o GIMP
  pages/page-01.png          # vista plana
```

## Funciones de una secuencia

Una secuencia vive en `panels/<nombre>`, con `sequence.json` y los paneles `panel-01.png`, `panel-02.png`, y los que sigan. Todo pasa por `novel_sequence`:

- `create`/`set` escriben `sequence.json`: acción, lugar, hora, `camera` (`static` o `moving`), `shot`, `location`, `figures` (`[{name, character, pose}]`, de adelante hacia atrás), `blocking` por panel (`[{figure, box, pose, view}]`; `view` solo cuando esa figura muestra otro lado que el de la toma, por ejemplo `back` si se gira a mirar atrás), `beats` (una acción por panel), `characters`, `view`, `effects`, `shadow` (`{back, lean, strength}`: hacia dónde cae la sombra de cada figura en el suelo; `back` positivo sube por la imagen, `lean` negativo va a la izquierda), `horizon` (la línea del horizonte, de 0 a 1 desde arriba) y, si se quiere, `brush_box` sobre `brush_source` para fijar el pincel. Una caja puede pasar el borde de abajo del cuadro cuando la figura sale por los pies. Un `sequence.json` viejo con `poses` se convierte en `figures`, y uno sin `blocked` da por aprobado el bloqueo de los paneles ya aceptados. Cambiar una caja o una pose con `set` pide otra vez el sí de ese panel, quita las figuras que ya tenía y la aceptación de ese panel y de los que siguen. Cambiar solo el horizonte pide el sí otra vez en los paneles que no están aceptados.
- `frame` da el siguiente paso del panel n: primero la placa, después el bloqueo (`needs_approval`, con la vista previa `panel-NN-blocking.png`, las cajas, las alturas, `ground_ratio` y `scale_off`) y después una figura a la vez. En cámara quieta, `ground_ratio` es la proporción del suelo de los paneles ya aceptados; el alto de una figura nueva sale de sus pies y de esa proporción. Una figura no tiene job hasta que la placa y el panel n-1 están aceptados y el bloqueo del panel n está aprobado.
- `stage` vuelve a dibujar la vista previa del bloqueo: un monigote por figura sobre la placa, en el orden en que entran, el horizonte y la altura de cada una.
- `place` instala la toma de un paso (`step`: `plate`, `figure`, `filter` o `panel`; `verdict`: `pending`, `accepted` o `rejected`). Cuando entra la última figura, la base se copia a `panel-NN.png`. Cambiar una placa o el panel n quita la aceptación de los que siguen, porque se pintaron desde un cuadro que ya no existe.
- `filter` arma el job del filtro sobre el panel aceptado y lo escribe en `panel-NN-fx.png`.
- `accept` acepta una placa, un bloqueo (`step: blocking`, solo después del sí de la persona), una figura o un panel, en orden.

Estas dos funciones mantienen la consistencia. Se le piden al agente, y el agente las propone cuando la secuencia se desvía.

**Revisar las referencias** (`op review`). Compara el panel con el anterior, zona por zona (cielo, centro, suelo, izquierda, derecha), y deja una tira lado a lado. El informe dice qué se movió en la cámara, el camino, el cielo, las poses, el lado de cada marca y la escala de la cabeza, antes de volver a dibujar.

**Unificar el arte** (`op unify`). Repinta de adelante hacia atrás. El panel anterior aceptado lleva el arte: el mismo pincel, la misma paleta y el mismo acabado. El panel que se unifica lleva la cámara y la acción, y no se redibuja de cero. El recorte de pincel acompaña, no reemplaza el cuadro. Un panel que no heredó el arte del anterior no se acepta y no se usa para el que sigue.

## Pruebas

```bash
.venv/bin/python -m pytest -q
```

No hace falta GPU. Las pruebas comprueban, entre otras cosas:

- que la cláusula del personaje es la misma en la hoja y en la viñeta;
- que ninguna viñeta de página mira otra;
- que un panel de secuencia no se dibuja sin el anterior aceptado;
- que los efectos no llegan a ninguna imagen base y quedan en `settings.json`;
- que la placa no lleva personajes y una figura solo cambia su caja;
- que con cámara fija los paneles comparten la placa, y el alto de una figura nueva sale del suelo ya aceptado;
- que dos poses iguales en un panel se rechazan;
- que una marca cambia de lado del cuadro entre frente y espalda;
- que un boceto con dueño no llega a lugares ni al estilo;
- que una toma rechazada queda guardada;
- que `novel_edit` no toca un píxel fuera de la región;
- que el `.ora` abre como capas separadas;
- que cambiar un diálogo no vuelve a dibujar.
