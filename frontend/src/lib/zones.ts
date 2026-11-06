import type { Lang } from './format'
import { TEAM_TZ, zoneOffsetMin } from './ownerZone'

/*
 * Time-zone names for the picker and every "{time} {city}" line (design v21 core.js "zones",
 * owner-setup §2). The city comes from this bilingual table; a zone not in it shows its IANA
 * city in Latin letters. The zone's name comes from Intl (longGeneric) in the UI language.
 * Offsets are figures: "GMT+3" in English, "غرينتش +3" in Arabic, with a true minus, wrapped in
 * an LTR isolate so they read the same in both directions.
 */

const LRI = '⁦'
const PDI = '⁩'
const MINUS = '−'

const CITY: Record<string, readonly [string, string]> = {
  [TEAM_TZ]: ['Baghdad', 'بغداد'], 'Asia/Riyadh': ['Riyadh', 'الرياض'], 'Asia/Kuwait': ['Kuwait', 'الكويت'],
  'Asia/Qatar': ['Doha', 'الدوحة'], 'Asia/Bahrain': ['Bahrain', 'البحرين'], 'Asia/Dubai': ['Dubai', 'دبي'],
  'Asia/Muscat': ['Muscat', 'مسقط'], 'Asia/Amman': ['Amman', 'عمّان'], 'Asia/Beirut': ['Beirut', 'بيروت'],
  'Asia/Damascus': ['Damascus', 'دمشق'], 'Asia/Jerusalem': ['Jerusalem', 'القدس'], 'Asia/Aden': ['Aden', 'عدن'],
  'Asia/Tehran': ['Tehran', 'طهران'], 'Europe/Istanbul': ['Istanbul', 'إسطنبول'], 'Africa/Cairo': ['Cairo', 'القاهرة'],
  'Africa/Khartoum': ['Khartoum', 'الخرطوم'], 'Africa/Tripoli': ['Tripoli', 'طرابلس'], 'Africa/Tunis': ['Tunis', 'تونس'],
  'Africa/Algiers': ['Algiers', 'الجزائر'], 'Africa/Casablanca': ['Casablanca', 'الدار البيضاء'],
  'Europe/London': ['London', 'لندن'], 'Europe/Dublin': ['Dublin', 'دبلن'], 'Europe/Lisbon': ['Lisbon', 'لشبونة'],
  'Europe/Paris': ['Paris', 'باريس'], 'Europe/Berlin': ['Berlin', 'برلين'], 'Europe/Amsterdam': ['Amsterdam', 'أمستردام'],
  'Europe/Brussels': ['Brussels', 'بروكسل'], 'Europe/Madrid': ['Madrid', 'مدريد'], 'Europe/Rome': ['Rome', 'روما'],
  'Europe/Zurich': ['Zurich', 'زيورخ'], 'Europe/Vienna': ['Vienna', 'فيينا'], 'Europe/Stockholm': ['Stockholm', 'ستوكهولم'],
  'Europe/Athens': ['Athens', 'أثينا'], 'Europe/Helsinki': ['Helsinki', 'هلسنكي'], 'Europe/Warsaw': ['Warsaw', 'وارسو'],
  'Europe/Kyiv': ['Kyiv', 'كييف'], 'Europe/Moscow': ['Moscow', 'موسكو'],
  'Asia/Karachi': ['Karachi', 'كراتشي'], 'Asia/Kolkata': ['Kolkata', 'كولكاتا'], 'Asia/Dhaka': ['Dhaka', 'دكا'],
  'Asia/Bangkok': ['Bangkok', 'بانكوك'], 'Asia/Jakarta': ['Jakarta', 'جاكرتا'], 'Asia/Kuala_Lumpur': ['Kuala Lumpur', 'كوالالمبور'],
  'Asia/Singapore': ['Singapore', 'سنغافورة'], 'Asia/Hong_Kong': ['Hong Kong', 'هونغ كونغ'], 'Asia/Shanghai': ['Shanghai', 'شنغهاي'],
  'Asia/Taipei': ['Taipei', 'تايبيه'], 'Asia/Seoul': ['Seoul', 'سول'], 'Asia/Tokyo': ['Tokyo', 'طوكيو'],
  'Asia/Baku': ['Baku', 'باكو'], 'Asia/Tbilisi': ['Tbilisi', 'تبليسي'], 'Asia/Kabul': ['Kabul', 'كابول'], 'Asia/Tashkent': ['Tashkent', 'طشقند'],
  'America/New_York': ['New York', 'نيويورك'], 'America/Chicago': ['Chicago', 'شيكاغو'], 'America/Denver': ['Denver', 'دنفر'],
  'America/Los_Angeles': ['Los Angeles', 'لوس أنجلوس'], 'America/Toronto': ['Toronto', 'تورنتو'], 'America/Vancouver': ['Vancouver', 'فانكوفر'],
  'America/Mexico_City': ['Mexico City', 'مكسيكو سيتي'], 'America/Sao_Paulo': ['São Paulo', 'ساو باولو'],
  'America/Argentina/Buenos_Aires': ['Buenos Aires', 'بوينس آيرس'], 'America/Bogota': ['Bogotá', 'بوغوتا'],
  'Pacific/Honolulu': ['Honolulu', 'هونولولو'], 'Africa/Lagos': ['Lagos', 'لاغوس'], 'Africa/Nairobi': ['Nairobi', 'نيروبي'],
  'Africa/Johannesburg': ['Johannesburg', 'جوهانسبرغ'], 'Australia/Sydney': ['Sydney', 'سيدني'], 'Australia/Perth': ['Perth', 'بيرث'],
  'Pacific/Auckland': ['Auckland', 'أوكلاند'], UTC: ['UTC', 'UTC'],
}

/** Every IANA zone this browser knows, plus UTC. */
export function allZones(): string[] {
  try {
    const list = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone')
    if (list && list.length) return list.includes('UTC') ? list : [...list, 'UTC']
  } catch {
    // older engines
  }
  return Object.keys(CITY)
}

/** "London" / "لندن"; a zone outside the table shows its IANA city ("Port Moresby"). */
export function zoneCity(zone: string, lang: Lang): string {
  const c = CITY[zone]
  if (c) return lang === 'ar' ? c[1] : c[0]
  return zoneCityLatin(zone)
}

export function zoneCityLatin(zone: string): string {
  const c = CITY[zone]
  return c ? c[0] : (zone.split('/').pop() ?? zone).replace(/_/g, ' ')
}

/** Whether the city has a translated name (otherwise it is Latin even in Arabic). */
export function hasLocalCity(zone: string): boolean {
  return zone in CITY
}

const nameCache = new Map<string, string>()
/** The zone's name in the UI language: "Arabian Standard Time" / "التوقيت العربي الرسمي". */
export function zoneName(zone: string, lang: Lang): string {
  const key = `${lang}|${zone}`
  const hit = nameCache.get(key)
  if (hit !== undefined) return hit
  const locale = lang === 'ar' ? 'ar' : 'en-GB'
  let name = zone
  try {
    name =
      new Intl.DateTimeFormat(locale, { timeZone: zone, timeZoneName: zone === 'UTC' ? 'long' : 'longGeneric' })
        .formatToParts(new Date())
        .find((p) => p.type === 'timeZoneName')?.value ?? zone
  } catch {
    // keep the id
  }
  nameCache.set(key, name)
  return name
}

/** "+3", "+5:30", "−5" (true minus), no isolate: the searchable form. */
export function offsetDigits(minutes: number): string {
  const a = Math.abs(minutes)
  const h = Math.floor(a / 60)
  const m = a % 60
  return `${minutes < 0 ? MINUS : '+'}${h}${m ? `:${String(m).padStart(2, '0')}` : ''}`
}

/** "GMT+3" / "غرينتش +3", the figure as an LTR isolate. */
export function zoneOffset(zone: string, lang: Lang, at: number = Date.now()): string {
  const f = `${LRI}${offsetDigits(zoneOffsetMin(zone, at))}${PDI}`
  return lang === 'ar' ? `غرينتش ${f}` : `GMT${f}`
}

/** Minutes the zone is ahead of the team's clock (negative = behind). */
export function diffFromTeam(zone: string, at: number = Date.now()): number {
  return zoneOffsetMin(zone, at) - zoneOffsetMin(TEAM_TZ, at)
}

/** "3 hours", "2 hours 30 minutes"; Arabic with real plural forms. */
export function spanWords(minutes: number, lang: Lang): string {
  const a = Math.abs(minutes)
  const h = Math.floor(a / 60)
  const m = a % 60
  const fig = (n: number) => `${LRI}${n}${PDI}`
  if (lang === 'ar') {
    const pr = new Intl.PluralRules('ar')
    const hw = (n: number) =>
      ({ one: 'ساعة واحدة', two: 'ساعتين', few: `${fig(n)} ساعات` } as Record<string, string>)[pr.select(n)] ?? `${fig(n)} ساعة`
    const mw = (n: number) =>
      ({ one: 'دقيقة واحدة', two: 'دقيقتين', few: `${fig(n)} دقائق` } as Record<string, string>)[pr.select(n)] ?? `${fig(n)} دقيقة`
    return h && m ? `${hw(h)} و${mw(m)}` : h ? hw(h) : mw(m)
  }
  const hw = (n: number) => `${fig(n)} ${n === 1 ? 'hour' : 'hours'}`
  const mw = (n: number) => `${fig(n)} ${n === 1 ? 'minute' : 'minutes'}`
  return h && m ? `${hw(h)} ${mw(m)}` : h ? hw(h) : mw(m)
}

/** Minutes after local midnight of `zone` for an instant. */
export function minuteOfDay(at: number, zone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(
    new Date(at),
  )
  const n = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  return n('hour') * 60 + n('minute')
}

/** The zone this browser (this Mac) is set to. */
export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return TEAM_TZ
  }
}
