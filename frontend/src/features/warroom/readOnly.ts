import { createContext, useContext } from 'react'

/*
 * Replay (ATL-206): the War Room as it was. Inside a read-only provider no message can act: no
 * Reply or context menu, and proposal cards draw as posted, with no Approve, Reject, Edit or Ask
 * the team and no live refetch. Reasoning disclosures still open.
 */
export const ReadOnlyContext = createContext(false)

export const useReadOnly = () => useContext(ReadOnlyContext)
