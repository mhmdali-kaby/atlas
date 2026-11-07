import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'
import '../i18n'

/*
 * ATL-258: findBy* and waitFor give up after 1000 ms by default. Under a full parallel run (10
 * workers plus Storybook's Chromium, or two runs at once) a first render or a fake-fetch round
 * trip in jsdom can take longer than that on a busy core, so the query failed although the
 * page was about to show the element. A waitFor returns as soon as its callback passes, so a
 * longer ceiling costs nothing on a passing test; 8000 ms stays well under the unit project's
 * 15 s test timeout, so a genuinely missing element still fails with the query's own message.
 */
configure({ asyncUtilTimeout: 8000 })
