"""Respuestas rápidas del equipo (documento "RESPUESTA PREDETERMINADAS SIDHE").

Carga las respuestas que ya usan los asesores, con su texto tal cual, y quita
las iniciales de la 0004 que cubrían el mismo tema con otras palabras: dos
versiones de la misma respuesta confunden a quien las elige.

Nada de lo que un asesor haya creado o editado se toca:
- las altas usan ON CONFLICT (atajo) DO NOTHING;
- las bajas y el renombre solo aplican si el texto sigue siendo el original.

La respuesta ENVIO no está aquí a propósito: incluye la cuenta bancaria para
el pago del envío, y eso no debe vivir en el repositorio de código. Se da de
alta desde el panel.

Revision ID: 0005
Revises: 0004
"""

import importlib.util
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RESPUESTAS_EQUIPO = [
    ('ayuda', 'Hola qué tal ¿Cómo podemos ayudarte?'),
    ('cita', 'Con gusto\n\nAntes de agendar una cita, asegúrate de que el paciente cuente con marcha autónoma (es decir, que pueda caminar por sí mismo).\n\nPara programar tu cita, necesitamos que nos compartas estos datos con al menos un día de anticipación:\n-Sucursal a la que deseas asistir\n-Día y horario preferido\n-Nombre completo del paciente\n-Teléfono de contacto\n\nEs muy importante que nos envíes toda esta información para poder confirmar tu cita.\n\n-Horarios de atención: lunes a domingo, de 11:00 a.m. a 9:00 p.m.\n\n¡Gracias por tu interés en SIDHE 3D!'),
    ('agendado', 'Buen día.\n\nAgendamos tu cita! le compartimos este link para que revise los requisitos para antes de acudir a su estudio: https://www.youtube.com/shorts/GlaxJxQaE5s'),
    ('costo', 'La valoración inicial que es impartida por fisioterapeutas, no tiene costo alguno.\n\nPlantilla impresa en 3D (Plantilla Personalizada) Costo: $2,199\nEl Plan Ortesico Plantar Costo: $3399 MX\nSandalia Del futuro Costo: $3799 MX.\nPlantilla Deportiva (Personalizada) Costo: $2,499 MX\nPlantilla Inteligente (con geolocalización) Costo: $2,299 MX\nPlantilla Express (NUEVO) Costo: $2,899 MXN\nPlan Familiar (NUEVO) Costo: $4,998 MXN'),
    ('forma-de-pago', 'El pago puede ser en una sola exhibición o a 3-6 MSI y puede realizarse con:\n-Tarjetas Liverpool (1 ó 6MSI)\n-Tarjetas de crédito/ débito de cualquier banco (1 ó 3MSI)\n-Efectivo'),
    ('horario', 'Estamos en horario Liverpool: lunes a domingo de 11:00 am a 9:00 pm'),
    ('sucursales', 'Nos encontramos únicamente en Liverpool:\nLiverpool Lindavista\nLiverpool Santa Fe\nLiverpool Delta\nLiverpool Insurgentes\nLiverpool Polanco\nLiverpool Perisur\nLiverpool Mitikah\nLiverpool Satélite\nLiverpool Galerías Pachuca\nLiverpool Angelópolis Puebla\nLiverpool Andares Guadalajara\nLiverpool Galerías Monterrey\nLiverpool Península Tijuana\nLiverpool Plaza Mayor León\nLiverpool Galerías Mérida\nLiverpool Las Américas Veracruz\nLiverpool Antea Querétaro\nLiverpool Galerías Cuernavaca\nLiverpool Crystal Tuxtla\nLiverpool Plaza Oaxaca, Oaxaca\nLiverpool Las Américas Morelia\nLiverpool Altabrisa Villahermosa\nLiverpool Altaria Aguascalientes\nLiverpool La Perla Guadalajara\nLiverpool San Luis, San Luis Potosí\nLiverpool Xalapa, Veracruz\nLiverpool Atizapán, Edo. Mex.'),
    ('tiempo-de-entrega', 'El tiempo de entrega es de 10 a 15 días hábiles sujeto a la demanda.'),
    ('plantillas-listas', 'Buen día.\n\nSus plantillas ya se encuentran listas. Puede pasar por ellas dentro de nuestro horario de lunes a domingo de 11am a 8pm'),
    ('proceso', 'Realizamos un escaneo 3D de tus pies, por lo que no utilizamos moldes de yeso. También hacemos una baropodometría para analizar cómo distribuyes el peso y detectar zonas de mayor presión. Con estos resultados, el fisioterapeuta diseña tus plantillas de forma personalizada.\n\n¿Te gustaría agendar tu estudio de pisada sin costo?'),
    ('material', 'Manejamos un material llamado "TPU".\n\nUn material sustentable que por sus propiedades puede hacer la plantilla tan rígida o tan flexible como se desee en una misma impresión.'),
    ('beneficio', 'Usar plantillas ortopédicas puede traer varios beneficios, sobre todo si pasas mucho tiempo de pie, caminas bastante o tienes alguna molestia en pies, rodillas o espalda.\n\nBeneficios principales:\n- Mejoran la postura: ayudan a alinear correctamente pies, tobillos, rodillas y cadera, lo que se refleja en una postura más estable y natural.\n- Reducen dolor: son muy efectivas para disminuir dolor en pies, talones, rodillas, cadera y espalda baja.\n- Corrigen o compensan pisada: si tienes pisada pronadora, supinadora o pie plano/arco alto, las plantillas ayudan a repartir mejor el peso y evitar sobrecargas.\n- Previenen lesiones: al absorber impactos y mejorar la biomecánica, reducen el riesgo de lesiones en actividades diarias o deportivas.\n- Más comodidad al caminar: distribuyen mejor la presión del pie, evitando callosidades, ardor o cansancio excesivo.'),
    ('plantilla-3d', 'En SIDHE 3D hacemos plantillas personalizadas con tecnología de impresión 3D.\n\nNo son plantillas genéricas: se diseñan específicamente para ti, a partir de un estudio completo de tu pisada.\n\nUtilizamos escáner 3D y baropodómetro para analizar la forma de tus pies, tus medidas y cómo distribuyes el peso al caminar o estar de pie.\n\nUn fisioterapeuta interpreta los resultados y diseña la plantilla según tus necesidades reales.\n\nEl resultado es una plantilla hecha a tu medida, pensada para acompañar tu cuerpo en el día a día.\n\nCosto: $2,199'),
    ('plantilla-deportiva', 'En SIDHE 3D contamos con plantillas deportivas personalizadas, diseñadas especialmente para personas activas y deportes de alto impacto.\n\nNo son genéricas ni de confort.\n\nCada par se diseña a partir de un estudio completo de pisada, realizado y analizado por fisioterapeutas.\n\nCon esos resultados, un fisioterapeuta diseña tu plantilla deportiva, definiendo densidad, soporte, barras y ajustes necesarios según tu actividad (correr, entrenar, deportes de impacto, etc.).\n\nEl objetivo es ayudarte a:\n– Reducir impacto y fatiga\n– Mejorar estabilidad y rendimiento\n– Prevenir lesiones\n– Acompañar tu cuerpo durante la actividad física\n\nCosto: $2,499 MX'),
    ('plantilla-express', 'La Plantilla Express SIDHE 3D incluye una valoración de pisada realizada por un fisioterapeuta certificado y el diseño personalizado de tus plantillas.\n\nCon base en el estudio, el par se diseña y fabrica el mismo día.\n\nLa plantilla queda lista ese día y se envía a domicilio al día hábil siguiente.\n\nEs una opción ideal si necesitas una solución personalizada en menos tiempo, manteniendo el mismo nivel profesional.\n\nAplica para compras realizadas de 11:00 a.m. a 5:00 p.m.\n\nCosto: $2,899 MXN'),
    ('plantilla-inteligente', 'La Plantilla Inteligente SIDHE 3D combina un estudio biomecánico personalizado con tecnología de geolocalización, para brindar soporte, alineación y mayor tranquilidad.\n\nCada par se diseña a partir de un estudio de pisada con escáner 3D y baropodómetro, interpretado por fisioterapeutas, quienes definen los soportes y ajustes según las necesidades reales del usuario.\n\n📡 Integra geolocalización compatible con iOS y Android, ideal para niños y para dar tranquilidad a padres y cuidadores.\n\nCosto: $2,299 MX'),
    ('sandalia', 'Esto no es una sandalia cualquiera…\n\nEs la sandalia del futuro: creada con tecnología de impresión 3D y diseñada con biomecánica para cada par se hace de forma artesanal en León, Gto.\n\nY lo mejor: se adapta a tus pies gracias al mismo análisis que usamos para nuestras plantillas personalizadas.\n\nTecnología + diseño mexicano.\n\nPerfectas para usar diario sin sacrificar tu salud ni tu estilo. ¿Estás listo para pisar diferente?\n\nCosto: $3799 MX.'),
    ('plan-ortesico', 'El plan de tratamiento está diseñado para acompañarte durante todo un proceso de mejora real.\n\nIncluye:\n– Una valoración inicial.\n– Dos revisiones de seguimiento con tu fisioterapeuta.\n– Los pares de plantillas que el especialista considere necesarios.\n\nTodo esto pensado para que tu pisada y tu postura evolucionen contigo.\n\nLa duración del tratamiento es de 1 año y 2 meses, y durante ese tiempo vamos adaptando lo que necesites para que camines mejor, vivas sin molestias y le saques el máximo a tu día.\n\nCosto: $3399 MX'),
    ('plan-familiar', 'El Plan Familiar SIDHE 3D está pensado para cuidar la pisada de toda la familia.\n\nIncluye un par de plantillas personalizadas para cada integrante (mamá, papá e hijos), todas diseñadas a partir de su propio estudio de pisada, realizado e interpretado por fisioterapeutas.\n\n-Cada miembro recibe su valoración individual y su plantilla hecha a la medida.\n-Aplica para hijos que acrediten parentesco o tutela legal.\n\nUna forma práctica de cuidar la salud plantar de la familia completa.\n\n-Costo: $4,998 MXN'),
]

# Iniciales de la 0004 que el documento del equipo reemplaza
REEMPLAZADAS = ("saludo", "agendar", "entrega", "listas", "estudio", "deporte")

# "/sucursal" (dar el teléfono) junto a "/sucursales" (la lista) se confunde
RENOMBRADAS = {"sucursal": "telefono-sucursal"}


def _iniciales() -> dict[str, str]:
    """Texto original de las respuestas de la 0004, para no borrar editadas."""
    ruta = Path(__file__).with_name("0004_bandeja_asesores.py")
    spec = importlib.util.spec_from_file_location("migracion_0004", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return dict(modulo.RESPUESTAS_INICIALES)


def upgrade() -> None:
    conexion = op.get_bind()
    iniciales = _iniciales()

    for atajo in REEMPLAZADAS:
        conexion.execute(
            sa.text("DELETE FROM respuestas_rapidas WHERE atajo = :a AND texto = :t"),
            {"a": atajo, "t": iniciales[atajo]},
        )

    for viejo, nuevo in RENOMBRADAS.items():
        conexion.execute(
            sa.text(
                "UPDATE respuestas_rapidas SET atajo = :nuevo "
                "WHERE atajo = :viejo AND texto = :t "
                "AND NOT EXISTS (SELECT 1 FROM respuestas_rapidas WHERE atajo = :nuevo)"
            ),
            {"viejo": viejo, "nuevo": nuevo, "t": iniciales[viejo]},
        )

    for atajo, texto in RESPUESTAS_EQUIPO:
        conexion.execute(
            sa.text(
                "INSERT INTO respuestas_rapidas (atajo, texto) VALUES (:a, :t) "
                "ON CONFLICT (atajo) DO NOTHING"
            ),
            {"a": atajo, "t": texto},
        )


def downgrade() -> None:
    conexion = op.get_bind()
    iniciales = _iniciales()

    for atajo, texto in RESPUESTAS_EQUIPO:
        conexion.execute(
            sa.text("DELETE FROM respuestas_rapidas WHERE atajo = :a AND texto = :t"),
            {"a": atajo, "t": texto},
        )
    for viejo, nuevo in RENOMBRADAS.items():
        conexion.execute(
            sa.text(
                "UPDATE respuestas_rapidas SET atajo = :viejo "
                "WHERE atajo = :nuevo AND texto = :t "
                "AND NOT EXISTS (SELECT 1 FROM respuestas_rapidas WHERE atajo = :viejo)"
            ),
            {"viejo": viejo, "nuevo": nuevo, "t": iniciales[viejo]},
        )
    for atajo in REEMPLAZADAS:
        conexion.execute(
            sa.text(
                "INSERT INTO respuestas_rapidas (atajo, texto) VALUES (:a, :t) "
                "ON CONFLICT (atajo) DO NOTHING"
            ),
            {"a": atajo, "t": iniciales[atajo]},
        )
