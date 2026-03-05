
# Comprender la máscara del horizonte La **máscara del horizonte** indica a Nova exactamente dónde se encuentran los obstáculos físicos en su ubicación específica. Utiliza una lista de puntos de coordenadas para dibujar una «línea del horizonte» que bloquea partes del cielo. Cada punto de la lista es un par de números: `[Azimut, Altitud]`.

  * **Azimut (0-360):** La dirección de la brújula. 0 es el norte, 90 es el este, 180 es el sur, etc. * **Altitud (0-90):** La altura de la obstrucción en grados en esa dirección.

## Viéndolo en acción Para que te hagas una idea, tomé los datos de mi propio muelle en el jardín (donde lucho contra una casa y algunos árboles altos) y los visualicé. ![Ejemplo de máscara de horizonte](/api/help/img/Horizonmask.jpeg) En este gráfico: * El **área marrón** es el cielo bloqueado definido por las coordenadas.
  * La **línea discontinua roja** es el umbral de altitud global (más información al respecto a continuación). * El **área azul** es su zona de imagen libre real. ## Cómo escribir su máscara Los datos se introducen como una simple lista de pares de coordenadas. No es necesario ser programador para hacerlo, solo hay que seguir el patrón.

**El formato de los datos:** ```text [[Azimuth, Altitude], [Azimuth, Altitude], ...] ``` **Ejemplo de mi jardín:** Estos son los datos sin procesar utilizados para generar el gráfico anterior. Puede copiar esta estructura y cambiar los números para que coincidan con su cielo:

```texto [[0,0, 0,0], [30,0, 30,0], [60,0, 36,0], [80,0, 25,0], [83,0, 30,0], [85,0, 20,0], 
[88.0, 0.0], [120.0, 30.0], [130.0, 20.0], [132.0, 0.0]] ```

### Reglas clave para una buena máscara 1. **Los puntos se conectan automáticamente:** Nova dibuja una línea recta entre cada punto que se indica. Si se define un punto en `[88, 0]` y el siguiente en `[120, 30]`, se crea una pendiente que los conecta.
2. **Utiliza «0» para romper obstrucciones:** dado que los puntos se conectan, debes volver a bajar la altitud a `0.0` para «terminar» una obstrucción. * *Observa en el ejemplo:* termino el primer bloque grande en `[88.0, 0.0]` y luego comienzo el siguiente pico.
3. **No es necesario utilizar los 360 grados completos:** No es necesario comenzar en 0 ni terminar en 360. Si solo hay un árbol grande entre los azimuts 140 y 160, solo hay que añadir puntos para esa zona específica. El resto del cielo permanecerá despejado por defecto. ## Importación desde Stellarium

Si utiliza Stellarium y tiene un archivo de horizonte `.hzn` o `.txt`, puede importarlo directamente en lugar de escribir los datos a mano. 1. Haga clic en el botón **Importar .hzn** situado debajo del área de texto Máscara de horizonte. 2. Seleccione su archivo de horizonte Stellarium (`.hzn` o `.txt`). 3. El archivo se analiza automáticamente y el campo Máscara de horizonte se rellena con los datos convertidos.

Las líneas de comentarios (que comienzan con «#» o «;») se ignoran. Si el archivo contiene más de 100 puntos de datos, se simplifica automáticamente para que los datos sean más ligeros. Los valores se redondean a un decimal y se ordenan por acimut. ## El «tiempo neto observable»

Es posible que observe una configuración en su configuración llamada **Umbral de altitud** (el valor predeterminado es 20 grados; puede configurarlo en «General»). * **Umbral de altitud:** es la altura mínima global que debe alcanzar un objeto para que se considere apto para la obtención de imágenes (para evitar la atmósfera densa/la suciedad cerca del horizonte).
  * **Máscara del horizonte:** recorta partes específicas del cielo *por encima* de ese umbral. Nova combina estas dos funciones inteligentes. Calcula el **tiempo observable neto**, lo que significa que solo cuenta el tiempo en el que el objeto está por encima de su límite global de 20° **Y** no está oculto detrás de las formas específicas de su máscara del horizonte. 