/**
 * Client portal wording, English and Spanish. The client picks the language
 * at the top of the portal; it is remembered on this device. Spanish is the
 * default when the browser is set to Spanish.
 */
import { useEffect, useState } from 'react'

const KEY = 'evs_portal_lang'

const EN = {
  dashboard: 'Dashboard', morning: 'Good morning', afternoon: 'Good afternoon', evening: 'Good evening',
  cal_connected: 'Calendar connected', cal_connect: 'Connect your calendar',
  banner: 'EvoSys finds and books your appointments.',
  demo_note: 'Sample view — every number here is example data so you can see how your portal works.',
  sample: 'Sample data',
  free3: 'The Free 3 — 3 booked appointments in 30 days, on us',
  pilot_line: 'Day {day} of 30 · {n} of 3 appointments delivered',
  ready: 'Ready to begin', pilot_wait: 'your 30 days start when EvoSys launches your campaign.',
  pending: 'Pending', elapsed: 'Time used: {p}%',
  first_title: 'Your first appointment starts here',
  first_text: 'EvoSys is finding your first appointments. You’ll see them here the moment one is booked.',
  view_business: 'View my business details',
  upcoming: 'Upcoming appointments', completed: 'Completed visits', estimates: 'Estimates sent', won: 'Won',
  mrv: 'Monthly recurring value', next3: 'Your next 3 appointments', view_all: 'View all',
  no_upcoming: 'No upcoming appointments right now.', update_result: 'Update result',
  open_est: '{n} open estimates', results_month: 'Results this month', appointments: 'Appointments', visits: 'Visits',
  estimates_short: 'Estimates', cust_type: 'Customer type', other: 'Other',
  report: 'Report an appointment result', report_none: 'Once an appointment is booked, you report how it went here.',
  who: 'Who', where: 'Where', when: 'When', notes: 'Notes',
  admin_only: 'Your company administrator records results.',
  visit_held: 'Visit held', no_show: 'No-show', reason: 'Choose a reason',
  ns_cancelled: 'They cancelled', ns_didnt: 'Didn’t show', ns_fit: 'Not a fit', ns_other: 'Other',
  estimate_sent: 'Estimate sent', amount: '$ Amount', won_opt: 'Won', one_time: 'One-time', recurring: 'Recurring',
  start_date: 'Start date', monthly_value: 'Monthly value', total_value: 'Total contract value',
  save_result: 'Save result', saving: 'Saving…', saved: 'Result saved — EvoSys can see it', saved_demo: 'Sample view — nothing was saved.',
  ask_evosys: 'Ask EvoSys to reschedule or follow up',
  toolbox: 'Your toolbox', toolbox_free: 'Included while you’re with EvoSys',
  toolbox_text: 'Run the rest of your business here too — one place for jobs, your crew and getting paid.',
  tb_jobs: 'Jobs', tb_schedule: 'Schedule', tb_team: 'Team', tb_customers: 'Customers', tb_invoices: 'Invoices', tb_ask: 'Ask us to turn it on',
  nav_dashboard: 'Dashboard', nav_appointments: 'Appointments', nav_results: 'Results', nav_estimates: 'Estimates & Contracts',
  nav_business: 'My Business', nav_support: 'Support', nav_more: 'More tools', powered: 'Powered by EvoSys Pro',
  setup_title: 'Finish your setup', setup_count: '{n} of 7 done', setup_start: 'Start setup', setup_continue: 'Continue setup',
  setup_lede: 'Tell us about your business so EvoSys books the right appointments. Each step saves as you go.',
  setup_done: 'Setup complete — EvoSys is reviewing your details.', setup_all_done: 'All steps done.',
  setup_left: '{n} steps left', setup_finish: 'Finish setup', save_continue: 'Save and continue',
  admin_only_setup: 'Your company administrator completes setup.',
  st_business: 'Business details', st_business_sub: 'Name, contact, phone, website',
  st_services: 'Services you offer', st_services_sub: 'What you clean',
  st_customers: 'Customers you want', st_customers_sub: 'Offices, medical, property managers…',
  st_area: 'Where you work', st_area_sub: 'Cities and ZIP codes',
  st_never: 'Never contact', st_never_sub: 'Your current customers',
  st_calendar: 'Connect your calendar', st_calendar_sub: 'Google or Microsoft 365',
  st_offer: 'Review and agree to your offer', st_offer_sub: 'Read-only · I agree',
  f_business: 'Business name', f_contact: 'Your name', f_phone: 'Phone', f_email: 'Email', f_website: 'Website (optional)',
  f_services_help: 'List what you offer, separated by commas.', f_services_ph: 'Office cleaning, deep cleaning, move-out cleaning',
  f_customers_help: 'Which customers are a good fit? Pick all that apply.',
  f_cities: 'Cities you serve', f_zips: 'ZIP codes (optional)',
  f_never_help: 'Current customers we should never contact — one per line: their email or website.',
  f_never_ph: 'manager@acmeoffice.com\nacmedental.com', f_never_have: '{n} already on your list.', f_never_none: 'I have none to add — continue',
  f_cal_help: 'EvoSys books appointments straight onto your calendar, only into times it shows as free.',
  f_cal_google: 'Connect Google Calendar', f_cal_ms: 'Connect Microsoft 365', f_cal_later: 'Do this later',
  f_cal_evosys: 'EvoSys will connect your calendar with you on your setup call.',
  offer_text: 'In your first 30 days, EvoSys finds businesses that need cleaning and books 3 appointments onto your calendar — at no cost to you. After that we talk about staying on.',
  offer_readonly: 'Set by EvoSys. You can’t change it here — just let us know if something looks wrong.',
  offer_agree: 'I agree', offer_agreed: 'You agreed on {d}.',
  tab_home: 'Home', tab_appts: 'Appointments', tab_results: 'Results', tab_business: 'Business', tab_support: 'Support',
}

const ES = {
  dashboard: 'Panel', morning: 'Buenos días', afternoon: 'Buenas tardes', evening: 'Buenas noches',
  cal_connected: 'Calendario conectado', cal_connect: 'Conecte su calendario',
  banner: 'EvoSys encuentra y agenda sus citas.',
  demo_note: 'Vista de muestra — cada número aquí es un ejemplo para que vea cómo funciona su portal.',
  sample: 'Datos de muestra',
  free3: 'Las 3 Gratis — 3 citas agendadas en 30 días, sin costo',
  pilot_line: 'Día {day} de 30 · {n} de 3 citas entregadas',
  ready: 'Listo para empezar', pilot_wait: 'sus 30 días empiezan cuando EvoSys lance su campaña.',
  pending: 'Pendiente', elapsed: 'Tiempo usado: {p}%',
  first_title: 'Su primera cita empieza aquí',
  first_text: 'EvoSys está buscando sus primeras citas. Las verá aquí en cuanto se agende una.',
  view_business: 'Ver los datos de mi negocio',
  upcoming: 'Próximas citas', completed: 'Visitas realizadas', estimates: 'Cotizaciones enviadas', won: 'Ganados',
  mrv: 'Valor mensual recurrente', next3: 'Sus próximas 3 citas', view_all: 'Ver todas',
  no_upcoming: 'No hay citas próximas por ahora.', update_result: 'Actualizar resultado',
  open_est: '{n} cotizaciones abiertas', results_month: 'Resultados de este mes', appointments: 'Citas', visits: 'Visitas',
  estimates_short: 'Cotizaciones', cust_type: 'Tipo de cliente', other: 'Otro',
  report: 'Reportar el resultado de una cita', report_none: 'Cuando se agende una cita, aquí reporta cómo le fue.',
  who: 'Quién', where: 'Dónde', when: 'Cuándo', notes: 'Notas',
  admin_only: 'El administrador de su empresa registra los resultados.',
  visit_held: 'Visita realizada', no_show: 'No se presentó', reason: 'Elija un motivo',
  ns_cancelled: 'Cancelaron', ns_didnt: 'No se presentaron', ns_fit: 'No es buen cliente', ns_other: 'Otro',
  estimate_sent: 'Cotización enviada', amount: '$ Monto', won_opt: 'Ganado', one_time: 'Una vez', recurring: 'Recurrente',
  start_date: 'Fecha de inicio', monthly_value: 'Valor mensual', total_value: 'Valor total del contrato',
  save_result: 'Guardar resultado', saving: 'Guardando…', saved: 'Resultado guardado — EvoSys ya lo ve', saved_demo: 'Vista de muestra — no se guardó nada.',
  ask_evosys: 'Pedir a EvoSys reprogramar o dar seguimiento',
  toolbox: 'Sus herramientas', toolbox_free: 'Incluidas mientras esté con EvoSys',
  toolbox_text: 'Maneje el resto de su negocio aquí también — un solo lugar para trabajos, su equipo y sus cobros.',
  tb_jobs: 'Trabajos', tb_schedule: 'Horario', tb_team: 'Equipo', tb_customers: 'Clientes', tb_invoices: 'Facturas', tb_ask: 'Pídanos activarlo',
  nav_dashboard: 'Panel', nav_appointments: 'Citas', nav_results: 'Resultados', nav_estimates: 'Cotizaciones y contratos',
  nav_business: 'Mi negocio', nav_support: 'Soporte', nav_more: 'Más herramientas', powered: 'Con la tecnología de EvoSys Pro',
  setup_title: 'Termine su configuración', setup_count: '{n} de 7 listos', setup_start: 'Empezar', setup_continue: 'Continuar',
  setup_lede: 'Cuéntenos de su negocio para que EvoSys agende las citas correctas. Cada paso se guarda solo.',
  setup_done: 'Configuración completa — EvoSys está revisando sus datos.', setup_all_done: 'Todos los pasos listos.',
  setup_left: 'Faltan {n} pasos', setup_finish: 'Terminar configuración', save_continue: 'Guardar y continuar',
  admin_only_setup: 'El administrador de su empresa completa la configuración.',
  st_business: 'Datos del negocio', st_business_sub: 'Nombre, contacto, teléfono, sitio web',
  st_services: 'Servicios que ofrece', st_services_sub: 'Qué limpia',
  st_customers: 'Clientes que busca', st_customers_sub: 'Oficinas, consultorios, administradoras…',
  st_area: 'Dónde trabaja', st_area_sub: 'Ciudades y códigos postales',
  st_never: 'No contactar', st_never_sub: 'Sus clientes actuales',
  st_calendar: 'Conecte su calendario', st_calendar_sub: 'Google o Microsoft 365',
  st_offer: 'Revise y acepte su oferta', st_offer_sub: 'Solo lectura · Acepto',
  f_business: 'Nombre del negocio', f_contact: 'Su nombre', f_phone: 'Teléfono', f_email: 'Correo', f_website: 'Sitio web (opcional)',
  f_services_help: 'Escriba lo que ofrece, separado por comas.', f_services_ph: 'Limpieza de oficinas, limpieza profunda, limpieza de mudanza',
  f_customers_help: '¿Qué clientes le convienen? Elija todos los que apliquen.',
  f_cities: 'Ciudades donde trabaja', f_zips: 'Códigos postales (opcional)',
  f_never_help: 'Clientes actuales que nunca debemos contactar — uno por línea: su correo o sitio web.',
  f_never_ph: 'manager@acmeoffice.com\nacmedental.com', f_never_have: 'Ya hay {n} en su lista.', f_never_none: 'No tengo ninguno — continuar',
  f_cal_help: 'EvoSys agenda las citas directo en su calendario, solo en horarios libres.',
  f_cal_google: 'Conectar Google Calendar', f_cal_ms: 'Conectar Microsoft 365', f_cal_later: 'Hacerlo después',
  f_cal_evosys: 'EvoSys conectará su calendario con usted en su llamada de configuración.',
  offer_text: 'En sus primeros 30 días, EvoSys encuentra negocios que necesitan limpieza y agenda 3 citas en su calendario — sin costo para usted. Después hablamos de continuar.',
  offer_readonly: 'Definida por EvoSys. No se puede cambiar aquí — avísenos si algo no se ve bien.',
  offer_agree: 'Acepto', offer_agreed: 'Aceptó el {d}.',
  tab_home: 'Inicio', tab_appts: 'Citas', tab_results: 'Resultados', tab_business: 'Negocio', tab_support: 'Soporte',
}

const TEXT = { en: EN, es: ES }

function initial() {
  try { const v = localStorage.getItem(KEY); if (v === 'en' || v === 'es') return v } catch { /* storage unavailable */ }
  return (typeof navigator !== 'undefined' && /^es\b/i.test(navigator.language || '')) ? 'es' : 'en'
}

let current = initial()
const listeners = new Set()

export function setLang(l) {
  current = l === 'es' ? 'es' : 'en'
  try { localStorage.setItem(KEY, current) } catch { /* storage unavailable */ }
  listeners.forEach(fn => fn(current))
}

export function tr(lang, key, vars = {}) {
  const s = (TEXT[lang] && TEXT[lang][key]) || EN[key] || key
  return s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ''))
}

export function useT() {
  const [lang, set] = useState(current)
  useEffect(() => { listeners.add(set); return () => listeners.delete(set) }, [])
  return { lang, t: (k, v, forceLang) => tr(forceLang || lang, k, v), setLang }
}
