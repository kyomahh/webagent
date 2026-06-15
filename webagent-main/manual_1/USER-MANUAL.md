# 4ga Boards User Manual

> Source: https://docs.4gaboards.com

---

## Getting started

Hello! This guide aims to provide a comprehensive tutorial for you to get started with 4ga Boards. In the upcoming sections we will show you all of the important features of our tool.

## What exactly is 4ga Boards?

4ga Boards is a convienient and easy to grasp management tool based on the kanban board technique. A kanban board is a visual tool used to manage and optimize workflows, which displays tasks as cards across columns that can represent different stages of a process. The main view of 4ga Boards, which you will probably see for the most time can be seen below.

[Image: mainviewgettingstarted_en-e6ac5245c2d24b3adf601cd30bf695de.png]

## [Creating an account](#account)

This section will guide you through creating your account in 4ga Boards.

## [Structure](#structure)

This section aims to present the logical structure of organizing the workflow with 4ga Boards.

4ga Boards is using a four-level structure to help you maintain even the most complicated projects:

[Project](#project) is a highest level of organization in 4ga Boards, a container holding boards.

[Board](#board) is a workspace within a project where tasks are organized and displayed using:

[List View](#list-view), where tasks are displayed in a to-do list style,

[Board View](#board-view), which is a classic Kanban view with lists and cards.

[List](#list) is a column on the board representing different stages or categories of the workflow (e.g. "To Do", "Doing"), with tasks attached to it.

[Card](#card) is representing individual task (with optional subtasks) that can be moved across lists.

Each of the levels is thoroughly explained in the respective sections.

[Sidebar](#sidebar) allows for efficient management of 4ga Boards program elements.

## [Administration and Settings](#admin-settings)

Here you will find explanation of all different features in the settings sections and administration panel.

- For each user:
[Settings](#settings) will show you your personal settings.

- For project managers:
[Project settings](#project-settings) will guide you through settings of projects inside your instance

- For administrators:
[Instance settings](#instance-settings) will explain the settings of the instance (e.g. demo.4gaboards.com)

## [Additional Information](#additional-info)

You should be able to find all the missing information from the previous chapters here, including FAQ.

If you have any further problems, feel free to [contact us](https://4gaboards.com/contact).

And if you don't have any troubles, or you simply love using our tool, consider starring us at: [4ga Boards Github](https://github.com/RARgames/4gaBoards)

---

## Installation and creating an account

# Installation and creating an account

If you succesfully deployed 4ga Boards or your organization has already provided you with working one, it is time to create your account. To do this, simply go to the web adress of your 4ga Boards.

To try out 4ga Boards, you can go and register in the [demo instance](https://demo.4gaboards.com/). It's limitations are described here: [Try 4ga Boards](https://4gaboards.com/try).

[Image: login_en-af5eb29e26706fed3a547d6671207548.png]

If you don't have an account yet, you can register by clicking "Create an account" button. Remember to choose a strong password and accept the "Terms of service" and Privacy Policy".

[Image: register_en-772018e97b5a56127f1f2a9c6fbb0878.png]

If you/your organization have enabled SSO sign-in you can register with your SSO provider (4ga Boards supports Google, Microsoft, GitHub and generic OIDC).

If you cannot register with both methods, it means the administrator of your instance disabled the registration. Ask your administrator to add you manually.

---

## Import/Export

## Import from Trello

Are you migrating from other software?

Currently 4ga Boards is supporting migration from Trello. To do this, export your Trello board in .json format (the only one included in the free version of Trello) and do the following:

- Create new board and select "Import":

[Image: boardcreate_en-dec32a5ab0362b083076298ee8be6f57.png]

- Select appriopriate option for import:

[Image]

- In the file manager select the appriopriate `.json` file, name your new board and choose in which project it should be created. Think of the project for now as kind of Trello's workspaces - a container that holds boards. More on project [here](#project).

[Image: importboardtrello_en-0c8c5b7334066e7008903d1f8c567cea.png]

And done! Now you have a fully functioning board - also with labels!

## Import from 4ga Boards

Changing instances or copying board from another user?

With the 4ga Boards import you can quickly setup your workspace. Be sure you have an appriopriate 4ga Boards export file (it should have a `.tgz` format) and do the following:

- Create new board and select "Import":

[Image: boardcreate_en-dec32a5ab0362b083076298ee8be6f57.png]

- Select appriopriate option for import:

[Image]

- In the file manager select the appriopriate .tgz file, name your new board and choose in which project it should be created. Here you can also check two options regarding users:

`Add project managers`: New managers will be added to the project if they had the same role in the exported board.

- `Create accounts for non-existing users`: New accounts will be created for users that does not exist in the current 4ga Boards instance, but were members of the board in the exported board.

[Image: importboard4ga_en-42f739f0c2b1e8364f975e133ea3f0c5.png]

## Export

Exporting in 4ga Boards is quick and easy. Simply open the context menu of the board you wish to export and select the `Export Board` option. Save the resulting `.tgz` file to your preferred location.

[Image: boardmenu_en-504faa17880e8a0cf3c6465fc648b2f8.png]

---

## Structure

# Structure

This section aims to present the logical structure of organizing the workflow with 4ga Boards.

4ga Boards is using a four-level structure to help you maintain even the most complicated projects. Speaking of projects...

[Project](#project) is a highest level of organization in 4ga Boards, a container holding boards.

[Board](#board) is a workspace within a project where tasks are organized and displayed using:

[List View](#list-view), where tasks are displayed in a to-do list style,

[Board View](#board-view), which is a classic Kanban view with lists and cards.

[List](#list) is a column on the board representing different stages or categories of the workflow (e.g. "To Do", "Doing"), with tasks attached to it.

[Card](#card) is representing individual task (with optional subtasks) that can be moved across lists.

Each of the levels is thoroughly explained in the respective sections.

[Sidebar](#sidebar) allows for efficient management of 4ga Boards program elements.

---

## Project

# Project

Projects are the highest structure of 4ga boards workflow. All of the projects can be accessed from the dashboard view (3) or using the sidebar (2).

[Image: projectsview_en-fb1477343dacf4f70eda20ce936649c8.png]

If you ever wish to come back to dashboard view, you can do so by clicking 4ga Boards logo in header (1). In addition, if you have administration rights to any of the projects, you can customize them in [project settings](#project-settings).

To create a project, simply click on the `+Add project` button. This button is located at the bottom of the sidebar or at the top-right corner while you are in the dashboard. After clicking one of the buttons, the prompt for naming the project will appear.

[Image: projectsadd_en-2b66d947617eff25ca8cac32eb780de3.png]

NOTE: If you don't see the option to create project it means you lack the administrator rights. In this case you can only use the projects you were already added to by the administrator.

To access context menu for the projects (available for project managers) hover over the project name and click `ellipsis` button. In this menu you can rename your project, go to [project settings](#project-settings), check activity for the project (when it was created and last updated) or add a new [board](#board).

[Image: projectsmenu_en-74909a67e4470a6550d30825e7bd4ac3.png]

After you create your first project, click on its tile in the dashboard or on its name in the sidebar to see the [board view](#board).

---

## Board

The heart of 4ga Boards is, unsuprisingly, a board. Board view is the main view of this app - you will spend here most of your time. Don't worry! It's easy to grasp.
At first you will see that your project contains no boards - to create them, simply click the `+Add Board` button that is located at the bottom of the sidebar or at the top-right corner of the screen.

[Image: boardviewempty_en-07726b7b56ac4c5f3992de859abd8020.png]

If you are joining to an existing project, here is how it should look like:

[Image: mainviewgettingstarted_en-e6ac5245c2d24b3adf601cd30bf695de.png]

If you have set the default view as list view, here is what you will see:

[Image: listview_en-c26bbc2fc4100bada795e3162a7fd767.png]

Notice that selected board is highlighted in the sidebar view (in this case, it is "New Website" from "Marketing" project).

## Creating a new board

There can be more than one board per project - simply click the `+Add Board` button that is located at the top-right corner of the screen to create new one inside the currently opened project. Alternatively you can add board using three-dot sidebar menu of a [project](#project) (it will create the board inside the selected project). The last option is to click the `+Add Board` button at the bottom of the sidebar. This will enable additional setting - choosing the project in which the board will be created from the dropdown list.

[Image: boardaddbutton_en-aac00e89337ce0287096168656a167a9.png]

This will open up a pop-up window in which you can name your board, prefill the lists in the board with templates or import your data from 4ga Boards (in .csv file format) or from Trello (supporting .json file format).

[Image: boardcreate_en-dec32a5ab0362b083076298ee8be6f57.png]

Currently there are two available templates, simple:

[Image: boardsimple_en-0f292f3e2a9e4a289b9f9812a6ed05e3.png]

And kanban:

[Image: boardkanban_en-0d8d53e96c30dd857eccd63f138e8d29.png]

## Board additional options

If you want to edit or delete your board, open the ellipsis menu in the sidebar (they will show after you hover over the board name). You can also change the order of the board within the project after clicking and holding the two arrows button that will appear on the left of the board name. If you wish, you can also export your board in .csv format here.

[Image: boardmenu_en-504faa17880e8a0cf3c6465fc648b2f8.png]

## Board toolbar

Each board comes with separate toolbar, in which (going from left to right) you can:

- Set up GitHub integration (click GitHub icon),

- See the number of cards after filtering,

- Add members to the board `+Add member` icon, delete or edit permissions of members (click on the appriopriate member icon to change it),

- Filter cards (more below the image),

- Change view (Board view/List view)

[Image: boardtoolbar_en-2eb3876798033c97133ed1b2a1a0795c.png]

## Board Filtering

Board filtering is a powerful tool that let's you quickly find what you are looking for. For even quicker navigation, you can select appriopriate option (explained below) by clicking or using key combination when you are clicked in the `Filter cards` type box.

You can filter board using different techniques:

- `Aa`: Match Case (`Alt` + `C`): will filter based on letter case (Example: typing "create" will not return cards with the title "Create")

- `~`: Any Match (`Alt` + `V`): "inclusive search"; Enable this option to show cards that match any of your selected filters.

(Example: If you select multiple members, the search will return every card that has at least one of the selected members assigned. If `Any Match` is off, only cards that have all selected members assigned will appear.)

- Filter by members: Select/Remove members you want to filter.

- Filter by labels: Select/Remove labels you want to filter.

- Filter by due date: Select the due date to filter: search will return all the cards that are *before* selected due date; if `Show Cards Just For Selected Day` option is enabled, it will show cards with only the *exact* due date. This search returns also the cards with appriopriate subtask due date.

## Board permissions

Each member of the board can have different permission:

- Project manager: manage boards and add members,

- Editor: can create and delete tasks and lists,

- Commenter: can view contents of the board and comment on the cards,

- Viewer: can only view contents of the board.

---

## Board View

# Board View

A classic Kanban board view, with [lists](#list) and [cards](#card).

[Image: mainviewgettingstarted_en-e6ac5245c2d24b3adf601cd30bf695de.png]

---

## List View

List View is a special view in 4ga Boards that lets you see all your tasks in a convenient to-do list style. Both views are interchangeable — all changes stay in sync, whether you make them in the board or list view.

[Image: listview_en-c26bbc2fc4100bada795e3162a7fd767.png]

## List view navigation

You can adjust the column width manually by dragging the `|` separators.

Each row represents a separate card and includes all the same features as a regular card in the board view, displayed in columns.
From left to right:

- `Bell` icon for notifications

- Card name

- Labels attached to the card

- Members attached to the card

- List in the board view where the card belongs

- `Tick` indicating whether a description is present

- Number of attachments

- Number of comments

- Due date

- Earliest due date

- Timer

- Tasks attached to the card

- `Ellipsis` menu of the card.

For additional info, please refer to [Card description](#card).

You can interact with each of them directly form the list view or by opening the appriopriate card. To do this, click on an empty spot in a card row (for ease of navigation, we recommend clicking on the description/attachment/comment field - they will not open a popup window).

[Image: listviewcard_en-2ed7ae71913c246c5a149c833aa06550.png]

This is the default column selection. To add or remove columns, use the `Ellipsis` menu in the top-right corner of the list. You can also set columns to adjust to the content or screen, and easily reset sorting and column selection to their default values.

[Image: listviewmenu_en-fb1abb984f938d09f473710d822a5858.png]

At the bottom of the list is another navigation pane, in which you can select how many cards are visible per page (default: 100), change pages with the arrows on the left and access list menu with the `cog` icon. On the right side you can add [List](#list) and [Card](#card) - they will also appear on the board view.

[Image]

## List view features

Compared to a classic board view, there are a few special options which helps visualise your workflow better:

Sorting - you can sort your list just like a regular table, in both ascending and descending order, indicated with `Arrow` icon.

You can perform a multilayered sorting - choose multiple columns to sort with, with the numbers indicating the priority. In the example below, the sorting order is as follows: labels ascending, then name descending.

[Image: listviewsorting_en-6ea6feedbeb304aa2afbca4ecdbe0a7e.png]

---

## List

In 4ga Boards, lists are columns on the board representing different stages or categories of the workflow. For example, kanban board template comes with 5 empty lists:

[Image: boardkanban_en-0d8d53e96c30dd857eccd63f138e8d29.png]

## List navigation

To move list into different spot on the same board, simply drag and drop it where you want. You can't, however, move a list to different board or project.

If you have many lists that don't fit in a browser window, you can move around by using `Shift` + `Scroll`, holding on an empty space in the board (when a hand icon is visible) or by using scrollbar at the bottom.

If the list is full of cards, you can scroll through them using mouse scroll (while hovering over the list) or with scrollbar at the right side of the list.

You can hide/unhide all lists (empty or with cards) by clicking on the little triangle button in the top-left corner of the list (right next to its name). This feature is very handy for decluttering your board view and/or hiding unnecesarry lists. Hidden list will have the number of holded cards displayed on them (see screenshot).

[Image: listhide_en-096ff901d7aa697b8a169187dcf68fd8.png]

## Creating, editing and deleting the list

To create a list, click on the `+add list` button on the board, write a name and confirm it by clicking on the green `Add List` button. It will automatically open a new pop-up window to create a next list. If you don't want to do it, simply click `Cancel` or start doing anything else and it will automatically close.

You can edit the list name by clicking on the `ellipsis` in the top-right corner of a list. It will open a context menu, in which you can also add card and delete list (clicking it will open an additional confirmation pop-up). In the same menu you can check activity of the list (when it was created and last updated). By clicking on `Add Card` you can create a new card at the bottom of the list.

[Image: listmenu_en-b9ba08ddfb792b3f6c162cb1792b5d9e.png]

## List in list view

Lists do not magically disappear when you switch from the board view to the list view. To see which list a card belongs to, refer to the "List" column. You can also move cards between lists here by clicking on it.

[Image: listinlistview_en-94a566d74897e8df73900b495a857104.png]

Sorting the lists in this view *does not* change its placement in the board view.

---

## Card

This section shows all of the possible actions that can be performed with a card, including labelling, notifications and extensive tutorial on description text editor.

Card represents individual task, e.g. "Go buy groceries" or "Fix the wrong button placement".

## Creating, moving and deleting cards

To create a card you have two options:

- Click on the `+ Add Card` button at the bottom of an existing list or `+` button at the bottom of an existing hidden list.

- Click the `ellipsis` icon at the top-right of the list and select `Add Card`. It will create card at the bottom of the list.

[Image: listmenu_en-b9ba08ddfb792b3f6c162cb1792b5d9e.png]

- While in list view, click on the `+ Add Card` button at the bottom navigation pane.

[Image]

After you write the title of the card, press `Enter` or `Add card` button to create it; alternatively, you can press `Ctrl` + `Enter` or `Ctrl` +`+Add Card` combination to create the card and immidiately open it. Pressing `Shift` + `Enter` or `Shift` +`+Add Card` will let you create multiple cards at once. To abort creating a new card, press `Cancel` or click in an empty spot on the board (this option will not work if you have filled the title; in that case, click the `Cancel` button).

The easiest way to move cards across the board is to drag and drop it in the desired place - just click and hold it! You can put card in both open and hidden lists. When dropping to the hidden list, it will be placed at the bottom of this list. To be sure which hidden list are you putting your card into, look if the title is pushed lower. In the example below, the card will be stored in "To test" list.

[Image: carddrop_en-8ebb388397211a4f8f85282112a254ac.png]

There are two other ways to move a card: with card menu (see below) and in the card view, right below the title (see Card options). You can also move card between lists in the list view by changing the "List" column.

[Image: listinlistview_en-94a566d74897e8df73900b495a857104.png]

To delete the card, you have to move your mouse above the card you want to delete. After that, a `ellipsis` icon will appear. Click on it to open a card menu and select `Delete Card`.

## Card menu

[Image: cardmenu_en-65bb66d60f8753e812c5c8492b09a56a.png]

After clicking the ellipsis menu in the card you will open a card menu. Here you can:

- Edit the name of the card

- Assing members to the card (see Card Options below)

- Add/remove labels to the card (see Card Options below)

- Edit due date of the card (see Card Options below)

- Edit timer of the card (see Card Options below)

- Move card to another project/board/list (note that this is the only way to move the card to another project or board, as drag & dropping the card works only on the lists in a current board)

- Duplicate card, which will create exact same copy of the card directly below it in the list

- Copy a link to a card which you can then share with your team

- Check activity on the card (creation, last edit time)

- Delete card

## Card View overview

When you click on the card, the card view will show up at the right side of your screen. Why not on the centre? The unique feature of 4ga Boards is that you can manipulate the lists and boards while the card view is open.

[Image: cardopenboardview_en-fa73e5a674a61bf854347d60440104c2.png]

It is quite convenient if you want to rearrange your cards while still writing the description, or to simply appraise you beautiful card cover picture. Isn't it beautiful?

[Image: cardopenboardviewlogo_en-2e0108b4a66ec2c96c2861e2724b10e1.png]

Also, notice that cards have different appearance that is changing with the amount of options and details set.

In the list view, the card is also opening at the right side, squeezing the table to fit the screen (no worries, it will be back at normal size once you close the card).

[Image: listviewcard_en-2ed7ae71913c246c5a149c833aa06550.png]

## Card options

The card view is the best way to change all the informations, options and details about your card. If the view gets cluttered, you can hide certain elements (description, tasks, attachments, comments) by clicking on the `-` icon next to them. Click the `+` icon to show them again. This section will explain all of them from the top to the bottom.

[Image: cardviewmain_en-3353501371dd15c371870e822e2b0b57.png]

- Notifications (`Bell` icon): Here you can subscribe or unsubscribe from the notifications on this card. While subscribed you will receive a notification (`bell` icon at the dashboard in the top-right corner) whenever someone adds comment to the card.

- Title: you can click on it to change the cards title (`ENTER` to accept)

- Card view icons on the top-right corner: click the `bin` icon to delete card (it will open pop-up to confirm deleting), three dots icon will open the card menu explained before, the `X` button will close the card view.

- List selection: Below the title of the card, it shows in which list the card is situated. Clicking on it will open dropdown list. Here you can select in which list the card should be moved (it will appear at the top of the list).

- Members: Click on the plus icon to toggle assigning members to the card. Tick symbol indicates whether the member is assinged or not.

[Image: cardmembers_en-33131266549af6e09bce8480b2788f4b.png]

Note that only members that are added to the current board can be assigned to the card. If you don't see a member that should be available here and you are a project manager, add the member in [board options](#board).
6. Labels: Click on the plus icon to add labels to your card. Tick symbol indicates whether or not the label is used. You can also manage your labels here: create new labels (click on `Create new label`) or editing existing ones (click on the `pencil` icon).

[Image: cardlabels_en-1d9effea891ad6a592a5d9490c30da16.png] [Image: cardlabelsedit_en-f7dc7db476b5cf2986fd76db3d2f1cde.png]

- Due date: Here you can add (click on `+` icon)/edit (`pencil` icon) the due date for the card. If the due date is furhter than two weeks, it will appear grey; if it is in the range of two weeks - yellow; if overdue - red.

[Image: cardduedate_en-87a16b61c10edada050dd24aadb64fbd.png]

- Timer: Using timer you can track the time it takes you to complete the task. Click on the timer to start/pause it, and click `pencil` button to edit the time manually or reset timer. When the timer runs it appears green.

- Earliest due date, from both main due date and all the subtasks due dates.

- Activity log (Created/Last updated time). Only visible if not disabled in the personal preferences.

- Description: Write the description for your card (to save click `Save` button or press `Ctrl` + `Enter`). See "Text editor" section below for a detailed guide.

- Tasks: In this section you can add/edit tasks in your card or mark them complete. A small line indicates the portion of tasks completed, ranging from red to green. It remains empty if there are no finished tasks or when there are no tasks added.

To add tasks, click the `+` icon or `Add Task` button.

- To toggle the task complete, click the square next to the task description.

- To rename the task, click on the task description; upon finishing press `Enter` or click `Save`.

- To delete task, hover over it until `ellipsis` icon appears; click it and select `delete task`.

- You can toogle tasks visibility on the board: to do this, click on the `triangle` icon near the cards taskbar visible on the list.

- Tasks can be assigned with a member or a due date: to do this click on the `ellipsis` icon and choose appriopriate option ("Edit Due Date" or "Edit Members"). This informations will be displayed at the right side of the appriopriate task. This can be done in both card view and directly on the board view (when tasks are expanded).

- Tasks activity log can be checked with `Check activity` fter clicking `ellipsis` icon.

- From the `ellipsis` menu, choose "Duplicate Task" to create duplicate.

[Image: cardtasksviewed_en-db9122228c46dd5bff94373953730fbe.png]

- Attachments: Here you can add attachments to your card by either `Ctrl` + `V`, dropping them on the card or clicking on `Add attachment` field and selecting from the disc. If the attachment is an image, you can use it as a cover that will appear in the list view. To do this, click on `Make cover` button near the desired image. To remove cover, use `Remove cover` button. To remove the attachment completely, hover over it, click on the `pencil` icon and select `Delete attachment`.

[Image: cardcover_en-6634036fbf191e8848e0cd6e33040c15.png]

- Comments: here you can add (start writing in the box and press `Ctrl` + `Enter` or `Add comment` button), edit (`pencil` icon) or delete (`bin` icon) coments to the card, using the same text editor as in the description field.

[Image: cardcomment_en-591275a3a9c6757f87c73f8ccd41ff3a.png]

You can also check activity by clicking the `waveform` symbol.

## Text editor

In 4ga Boards we are using very powerful markdown editor with unique features (marked by bold text). In the bottom-right corner of text editor box there are three small dots: drag them with a mouse to make the box bigger/smaller. The working view should look like this (notice the yellow text local changes - it shows that the text is still in edit mode and the changes are not yet registered on the server):

[Image: texteditor_en-4504d93f75978eb288f302203bb3e93d.png]

As the text editor is a markdown editor, it follows typical syntax to other editors like this. More detailed information on any of the options of the markdown editor can be found here: [basic syntax](https://www.markdownguide.org/basic-syntax/).

- Editor options are as follows (from left to right; you can click them or use button combination):

- Bold text (CTRL + b)

- Italic text (CTRL + i)

- Strikethorugh text (CTRL + SHIFT + x)

- Insert HR/horizontal bar (CTRL + h)

- Insert title (CTRL + number from 1 to 6, depending on the size); alternatively you can use hashtags.

- Add link (CTRL + l)

- Insert a quote (CTRL + q)

- Insert code (CTRL + j)

- Insert codeblocks (CTRL + SHIFT + j). After creating default codeblock, you can add **custom tags**:

The first tag just after opening symbol (`````) is a language shortcut, e.g. ````js` will highlight javascript syntax.

- The second tag is for showing line numbers ````js showLineNumbers`

- The third that indicates which lines should be highlighted ````js showLineNumbers {1, 3-4}` - in this example the lines 1, 3 and 4 highlighted.

- Insert comment (CTRL + /)

- Add image (CTRL + k)

- Add table

- Add unordered list (CTRL + SHIFT + u)

- Add ordered list (CTRL + SHIFT + o)

- Add checked list (CTRL + SHIFT + c)

- Open help (opens the [basic syntax](https://www.markdownguide.org/basic-syntax/) site)

- **Add issue link - with this feature you create a link from card to GitHub issue or pull request. Some ways to do it:**

click the add issue button and type issue or PR number or write: #(number of the issue), e.g. `#1`.

- instead of hashtag you can use: GH-(number), e.g. `GH-1`

- to link issue or PR in fork use: (fork name)#(issue number), e.g. `samplefork#1`

- to link issue or PR in specific repository use:(username or organization name)/(repository name)#(issue number), e.g. `RARgames/4gaboards#1`

- **Add colored text**

use button to select desired color or type in color name like in the example: `<!--black-->This text wil be black<!--black-end-->`

- available colors: black, grey, white, brown, red, purple, pink, green, lime, yellow, blue, cyan, orange.

View options:

- Edit code (ctrl + 7) - shows only the edited text with markdown symbols

- Live code (ctrl + 8) - shows both markdown symbols (on left) and live preview of the text (on the right)

- Preview code (ctrl + 9) - shows just the text preview

- Toggle fullscreen (ctrl + 0)

Some functions that don't have special buttons:

- **Add commit link** - type:

to link commit use (commit hash), e.g. `1d7e95e8d496564ac5f69a06db60df79a6a585c4`

- to link commit in fork use (fork name)@(commit hash), e.g. `samplefork@1d7e95e8d496564ac5f69a06db60df79a6a585c4`

- to link commit in repository use (username)/(repository name)@(commit hash), e.g. `RARgames/4gaBoards@1d7e95e8d496564ac5f69a06db60df79a6a585c4`

- **Add mention** - type:

to mention user use @(username), e.g. `@RARgames`

Alternatively you can paste links to link commit, commit comment, issue or PR, issue or PR comment, user.

Special power: if you have read so far, you can use special ability: add invisible comment with: `<!-- This comment will be invisible so I can say whatever I want -->`

---

## Sidebar

All the time while using the 4ga Boards you can use the sidebar. It let's you quickly manage and navigate thorough your projects and boards. Here is how it looks like:

[Image: sidebarmain_en-00973becab835d83f75d9057fdee8f8d.png]

### Some actions you can perform within sidebar:

- You can change sidebar width inside [personal settings](#settings).

You can show/hide the sidebar using the bar and arrow icon in the top-left corner, left of 4ga Boards logo.

- You can filter your projects or boards using the filtering function at the top of the sidebar. The two arrows icon with a letter indicates which structure is being filtered: projects (P) or boards (B). To switch, click on the icon. As you can see in the example below, filtering works also if you write the middle part of the word. To clear filtering, click on the "x" icon.

[Image]

- You can show/hide the boards in projects using triangle button on the left of the project name. It will show all of the boards within this project that you have access to (unless there is an active filtering hiding some of them).

- You can quickly navigate between different projects/boards by clicking on their names. Selected project/board will be highlighted with blueish color.

- You can edit project/board within sidebar using a three-dot menu (see [project](#project) and [board](#board)).

- If you have appriopriate permissions, the buttons `+ Add project` and `+ Add board` will be visible at the bottom of the sidebar.

---

## Notifications

4ga Boards features a comprehensive notifications sytem, consisting of two main modules: *Notification Center* and *Check Activity Log*

## Notifications and Notification Center

You can quickly view notifications by clicking the `bell` icon in the top-right corner. This will open a panel on the side. To close it, click anywhere on the board.

[Image: notifications_en-7182e9cc8b3071254f24457fe533c8b6.png]

Each notification is structured as follows:

- Notification category (see details below)

- User and time of trigerring notification

- Detailed description of notification (what *action* occurred on which *element*, e.g. *adding a user* to a *task*)

- Board and Project associated with the notification (if applicable)

- `eye` icon - notification is unread/`crossed eye` icon and dimmed notification - notification is read; click the icon to manually change the status

- `bin` icon - delete notification.

[Image: notificationone_en-88bfa9488beb1bffe0a4099be2a60b28.png]

There are several notification categories marked with a distinctive label on the left:

- **Project** (e.g. creation, name changes, adding/removing users, background changes)

- **Board** (e.g. creation, name changes, adding/removing users)

- **List** (e.g. creation, removal, position changes on the board, expanding/collapsing)

- **Card** (e.g. adding tasks, members, labels, comments, changing the description)

- **Task** (e.g. creation, marking as complete, adding members, setting a due date)

- **Comment** (adding, updating, deleting)

At the top of the panel, you will find a filter box that helps you quickly scan notifications.
Use the `double arrow` icon to change the filter type:

- **All** – shows all results

- **Project** – notifications for a specific project

- **Board** – notifications for a specific board

- **User** – notifications related to a user

- **Card** – notifications related to a card

- **Text/type of activity** – searches notification text fields (e.g. comment text) or activity type (e.g. typing "taskcreate" will result in showing all task creation notifications)

[Image]

Open `ellipsis` icon to mark all notifications as read/unread, delete all notifications, delete only read notifications. Click on the icon to the left from the `ellipsis` to open fullscreen Notification Center, where you can thoroughly examine all notifications.

[Image: notificationscenter_en-8824fbed73e7a63c4751a6322bf3d29c.png]

## Activity Check Log

In several elements, clicking the `ellipsis icon` gives you the option to check activity. This opens the Activity Log popup window.

[Image: activitylog_en-af3d75e81359fea0791d1d4e5f031614.png]

The window displays the creation and last update time of the element, along with all activity performed on it. For elements with high activity (e.g. boards), you can scroll through the activity list, down to the very first entry.

---

## Settings

The personal and general settings panel looks like this:

[Image: settingsgeneralsidebar_en-3b10346e0638adc3848d82bd5e1a29be.png]

## Profile

In the profile settings you can add an avatar (from the disc), change your displayed name and add additional informations such as phone and organization name. Remember to click on the `Save` button!

[Image: settingsprofile_en-547d015a3d81f2a0028e6e529889e752.png]

## Preferences

This section lets you customize 4ga Boards to your, well, preferences.

[Image: settingspreferences_en-d32e3e1f99759db5e322a4b6e94da3af.png]

### General

- "Language": Select the language you want to use in 4ga Boards.

- "Theme": Select the color theme of the application. Currently available themes: default (which is used in this documentation), and "GitHub Dark", and even create custom theme! (see pictures below)

[Image: githubdarkrounded_en-784aebfb210f284096f1a1f241f88cad.png]

[Image: themelight_en-706419706933d536bc39618941a95768.png]

[Image: themecustomselect_en-29920bcc2f798aa43f1544b40446bb25.png]

[Image: themecustom_en-47429ba480f5021e68c097d65e07b442.png]

- "Theme Shape": Select the shape of the application theme. Choose from default square shape or rounded shape on elements (see picture below)

- "Preferred Details Font": Select the font (default or monospace) you want to use in card description and comments; in this documentation, the default version is used.

- "Default View": Set if you want boards to open in board view or list view by default.

- "Compact Sidebar": choose the style of your sidebar (applicable for both settings and board sidebars).
If you enable it, you sidebar will be slim. This option is great if you prefer minimalistic design and more room for your lists in the board view.

[Image: sidebarslim_en-6e65da8fa3d7752b6c4dc8b34c53f1f9.png]

If you disable it, the sidebar will be bulkier, which might be useful if you have long project names.

[Image: sidebarnormal_en-ec0303baddc5b2aa2fb9a0f0af9e3a7a.png]

- "List View Style": Set if you want a compact or bigger (default) version of list view; in this documentation, the compact version is visible.

- "Users Settings Style": Set if you want a compact or bigger (default) version of User Settings;

- "Hide Card Modal Activity": Enable to hide activity log (creation and last update time) in card view.

- "Hide Closest Due Date": Hide closest/earliest due date in card modal.

### Notifications

- "Subscribe to my own cards": set if you want to be automatically subscribed to the cards created by you (see [card](#card#card-options), card options section).

- "Subscribe to new boards": set if you want to be automatically subscribed to boards you join

- "Subscribe to new projects": set if you want to be automatically subscribed to projects you join.

(ADMIN ONLY BELOW)

- "Subscribe to users notifications": set if you want to be automatically subscribed to notifications about other users (change e-mail, username).

- "Subscribe to instance notifications": set if you want to be automatically subscribed to instance-wide notifications.

## Account

Here you can change your username and/or e-mail adress. For both changes you will need to input your current password.

[Image: settingsaccount_en-75dd0952aae59a5d9e1a943c58b2e8c4.png]

## Authentication

Here you can change your current password.

[Image: settingsauth_en-a21711d7b98b81a6d837de5cb231f5b0.png]

## About

Section providing information about your current build version, latest available version of 4ga Boards and useful links: [4ga Boards](https://4gaboards.com), [Documentation](https://docs.4gaboards.com),[Support development button](https://www.paypal.com/donate/?hosted_button_id=86RVDTMNLBBPJ), [GitHub](https://github.com/RARgames/4gaBoards),[X](https://x.com/4gaBoards), [YouTube](https://www.youtube.com/@4gaBoards), [LinkedIn](https://www.linkedin.com/company/4ga-boards), [Facebook](https://www.facebook.com/4gaBoards), [Privacy Policy](https://4gaboards.com/privacy-policy) and [Terms of Service](https://4gaboards.com/terms-of-service). Here, you can also import the “Getting Started” project, which provides an interactive introduction to 4ga Boards features and functionality.

[Image: settingsabout_en-559bf713396b58e12feb3c236295fc8d.png]

---

## Views description

# Views description

- Dashboard view

[Image: projectsview_en-fb1477343dacf4f70eda20ce936649c8.png]

- Project view

[Image: projectview_en-c5b7878283a06954f176651dc85b6bd3.png]

- Board view

[Image: boardview_en-ebee2c6c3a2a8b7baa803b5c89283c91.png]

- Board with opened card view

[Image: cardview_en-823ddbd01c1c914dfe0aa0627f89097c.png]

---

## Useful Shortcuts

# Useful Shortcuts

This page presents shortcuts that helps you use 4ga Boards more effectively.

`Ctrl` + `Enter`

Use it to quickly save text changes, e.g. while editing description, comments or adding a task. If you use it while creating a new card it will automatically open card view.

`Shift` + `Enter`

Use this to quickly create multiple cards while adding a new one.

`Shift` + `Scroll`

Hold shift to scroll across the board horizontally. Very useful when a board has a lot of lists that don't fit in the browser window.

`Tab`

While typing, use this to switch between filtering types (notifications, sidebar)
