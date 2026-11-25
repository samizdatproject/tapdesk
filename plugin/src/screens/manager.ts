import {
  CreateStartUpPageContainer,
  ListContainerProperty,
  ListItemContainerProperty,
  MenuContainerProperty,
  MenuItemProperty,
  RebuildPageContainer,
  TextContainerProperty,
  TextContainerUpgrade,
  type EvenAppBridge,
} from '@evenrealities/even_hub_sdk'

export interface TextBox {
  name: string
  x: number
  y: number
  w: number
  h: number
  content: string
  capture?: boolean
  dim?: boolean
}

export interface ListBox {
  name: string
  x: number
  y: number
  w: number
  h: number
  items: string[]
}

// Shared page geometry (576×288): one-line header, body, one-line footer.
export const HEADER = { x: 0, y: 0, w: 576, h: 40 }
export const BODY = { x: 0, y: 40, w: 576, h: 208 }
export const FOOTER = { x: 0, y: 248, w: 576, h: 40 }

/** A whole glasses page. Exactly one container captures input: the list if any, else a text box. */
export interface Page {
  id: string
  texts: TextBox[]
  list?: ListBox
  menu?: { name: string; id: number }[]
}

const CREATE_TEXT_LIMIT = 1000
const UPGRADE_TEXT_LIMIT = 2000
const MIN_GAP_MS = 120 // first write after a change
const LIVE_GAP_MS = 500 // between writes to the glasses

/**
 * The only code that talks to the glasses display. Calls are serialised so a
 * rebuild never overtakes the create, and text upgrades are throttled because
 * the BLE queue is slow.
 */
export class ScreenManager {
  private page: Page | null = null
  private ids = new Map<string, number>()
  private created = false
  private chain: Promise<unknown> = Promise.resolve()
  private pending = new Map<string, string>()
  private timer: ReturnType<typeof setTimeout> | null = null
  private lastWrite = 0
  private gen = 0 // bumps per page so queued upgrades never hit the next page

  constructor(
    private readonly bridge: EvenAppBridge,
    private readonly onProblem: (message: string) => void,
  ) {}

  get pageId(): string | null {
    return this.page?.id ?? null
  }

  /** Resolves true once the page is on the glasses (and still the current one). */
  show(page: Page): Promise<boolean> {
    this.page = structuredClone(page)
    this.gen++
    this.pending.clear()
    this.ids = new Map()
    const textObject = page.texts.map((t, i) => {
      this.ids.set(t.name, i + 1)
      return new TextContainerProperty({
        xPosition: t.x,
        yPosition: t.y,
        width: t.w,
        height: t.h,
        borderWidth: 0,
        paddingLength: 4,
        containerID: i + 1,
        containerName: t.name,
        content: t.content.slice(0, CREATE_TEXT_LIMIT),
        isEventCapture: !page.list && t.capture ? 1 : 0,
        textColor: t.dim ? 2 : 4,
      })
    })
    const listObject = page.list
      ? [
          new ListContainerProperty({
            xPosition: page.list.x,
            yPosition: page.list.y,
            width: page.list.w,
            height: page.list.h,
            borderWidth: 0,
            paddingLength: 4,
            containerID: textObject.length + 1,
            containerName: page.list.name,
            isEventCapture: 1,
            itemContainer: new ListItemContainerProperty({
              itemCount: page.list.items.length,
              itemWidth: page.list.w,
              isItemSelectBorderEn: 1,
              itemName: page.list.items,
            }),
          }),
        ]
      : undefined
    const menuObject = page.menu?.length
      ? new MenuContainerProperty({
          menuItems: page.menu.map(m => new MenuItemProperty({ itemName: m.name, itemID: m.id })),
        })
      : undefined
    const spec = {
      containerTotalNum: textObject.length + (listObject?.length ?? 0),
      textObject,
      listObject,
      menuObject,
    }

    const gen = this.gen
    if (!this.created) {
      this.created = true
      return this.enqueue(async () => {
        const result = await this.bridge.createStartUpPageContainer(new CreateStartUpPageContainer(spec))
        console.log(`[tapdesk] createStartUpPageContainer(${page.id}) → ${result}`)
        if (result !== 0) {
          this.onProblem(
            `Glasses page could not be created (code ${result}). In the simulator, restart it and reload.`,
          )
        }
        return result === 0 && gen === this.gen
      }).then(Boolean)
    }
    return this.enqueue(async () => {
      const ok = await this.bridge.rebuildPageContainer(new RebuildPageContainer(spec))
      console.log(`[tapdesk] rebuildPageContainer(${page.id}) → ${ok}`)
      if (!ok) this.onProblem(`Glasses page "${page.id}" could not be redrawn.`)
      return ok && gen === this.gen
    }).then(Boolean)
  }

  /** Flicker-free text update on the current page; no-op if unchanged or absent. */
  setText(name: string, content: string): void {
    const box = this.page?.texts.find(t => t.name === name)
    if (!box || box.content === content) return
    box.content = content
    this.pending.set(name, content)
    if (this.timer) return
    const wait = Math.max(MIN_GAP_MS, LIVE_GAP_MS - (Date.now() - this.lastWrite))
    this.timer = setTimeout(() => this.flush(), wait)
  }

  /** Root-page exit: the OS asks the user to confirm. */
  exit(): void {
    void this.enqueue(() => this.bridge.shutDownPageContainer(1))
  }

  private flush(): void {
    this.timer = null
    this.lastWrite = Date.now()
    const gen = this.gen
    for (const [name, content] of this.pending) {
      const id = this.ids.get(name)
      if (id === undefined) continue
      void this.enqueue(async () => {
        if (gen !== this.gen) return
        await this.bridge.textContainerUpgrade(
          new TextContainerUpgrade({
            containerID: id,
            containerName: name,
            content: content.slice(0, UPGRADE_TEXT_LIMIT),
          }),
        )
      })
    }
    this.pending.clear()
  }

  /** Runs after every earlier call; resolves with the task's result, or undefined if it threw. */
  private enqueue<T>(task: () => Promise<T>): Promise<T | undefined> {
    const run = this.chain.then(task).catch(err => {
      console.error('[tapdesk] glasses call failed', err)
      return undefined
    })
    this.chain = run
    return run
  }
}
