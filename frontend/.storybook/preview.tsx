import type { Preview } from '@storybook/react-vite'
import '../src/i18n'
import '../src/index.css'
import { StoryFrame, type StoryDirection } from '../src/storybook/StoryFrame'
import { isThemeChoice } from '../src/theme/theme'

const preview: Preview = {
  parameters: {
    layout: 'fullscreen',
  },
  globalTypes: {
    theme: {
      description: 'Appearance',
      toolbar: {
        title: 'Theme',
        icon: 'mirror',
        items: [
          { value: 'light', title: 'Light', icon: 'sun' },
          { value: 'dark', title: 'Dark', icon: 'moon' },
          { value: 'system', title: 'System', icon: 'browser' },
        ],
        dynamicTitle: true,
      },
    },
    direction: {
      description: 'Text direction (RTL renders in Arabic)',
      toolbar: {
        title: 'Direction',
        icon: 'transfer',
        items: [
          { value: 'ltr', title: 'LTR · English' },
          { value: 'rtl', title: 'RTL · العربية' },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: {
    theme: 'light',
    direction: 'ltr',
  },
  decorators: [
    (Story, context) => {
      const theme = isThemeChoice(context.globals.theme) ? context.globals.theme : 'light'
      const direction: StoryDirection = context.globals.direction === 'rtl' ? 'rtl' : 'ltr'
      return (
        <StoryFrame theme={theme} direction={direction}>
          <Story />
        </StoryFrame>
      )
    },
  ],
}

export default preview
