const base =
  'inline-flex h-8 min-w-8 items-center justify-center gap-1.5 rounded-full text-subheadline font-medium text-label hover:bg-fill-3 aria-expanded:bg-fill-2 [&_svg]:size-[17px] [&_svg]:shrink-0'

/** Design shell.css .tb-btn: 32px capsule buttons inside the toolbar's glass group. */
export const toolbarButton = `${base} px-2.5`
/** .tb-btn.is-icon: a 32px circle. */
export const toolbarIconButton = `${base} w-8 px-0`
