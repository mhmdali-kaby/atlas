import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { excerptSource, parseBlocks, tokenize, type Piece, type Token } from './tokens'

/* Renders free text with its bidi islands (tokens.ts, design v4 War Room §9). */

/** "BTC-USDT-SWAP" as base + rest; an id with no "-" is all base. */
export function InstrumentId({ id }: { id: string }) {
  const cut = id.indexOf('-')
  return (
    <bdi dir="ltr" className="inst-tok">
      <span className="inst-base">{cut < 0 ? id : id.slice(0, cut)}</span>
      {cut >= 0 && <span className="inst-rest">{id.slice(cut)}</span>}
    </bdi>
  )
}

function TokenNode({ token, flat }: { token: Token; flat: boolean }) {
  const { t } = useTranslation('warroom')
  switch (token.type) {
    case 'code':
      return flat ? (
        <bdi dir="ltr" className="tok">
          {token.text}
        </bdi>
      ) : (
        <code dir="ltr">{token.text}</code>
      )
    case 'tag':
      return flat ? <>{t(`tag.${token.tag}`)}</> : <span className="status-tag">{t(`tag.${token.tag}`)}</span>
    case 'mention':
      return (
        <bdi dir="ltr" className={flat ? 'tok' : 'mention'}>
          {token.text}
        </bdi>
      )
    case 'inst':
      return flat ? (
        <bdi dir="ltr" className="tok">
          {token.text}
        </bdi>
      ) : (
        <InstrumentId id={token.text} />
      )
    case 'fig':
      return (
        <bdi dir="ltr" className={flat ? 'tok' : 'fig'}>
          {token.text}
        </bdi>
      )
  }
}

function renderPieces(pieces: Array<Piece>, flat: boolean): ReactNode[] {
  return pieces.map((piece, i) => {
    if (typeof piece === 'string') return <Fragment key={i}>{piece}</Fragment>
    if (piece.type === 'strong') return <strong key={i}>{renderPieces(piece.children, flat)}</strong>
    return <TokenNode key={i} token={piece} flat={flat} />
  })
}

/** One line of free text with its islands. */
export function Inline({ text, flat = false }: { text: string; flat?: boolean }) {
  return <>{renderPieces(tokenize(text, flat), flat)}</>
}

/** A one-line excerpt (reply quotes): the same islands, no bold, tags as words. */
export function Excerpt({ text }: { text: string }) {
  return <Inline text={excerptSource(text)} flat />
}

/** A message body: every paragraph and list decides its own direction. */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks = parseBlocks(text)
  return (
    <div className={className ? `md ${className}` : 'md'}>
      {blocks.map((block, i) => {
        if (block.type === 'p')
          return (
            <p key={i} dir="auto">
              <Inline text={block.text} />
            </p>
          )
        const List = block.type
        return (
          <List key={i} dir="auto">
            {block.items.map((item, j) => (
              <li key={j}>
                <Inline text={item} />
              </li>
            ))}
          </List>
        )
      })}
    </div>
  )
}
