# 4ga Boards Admin Manual

> Source: https://docs.4gaboards.com

---

## Administration and settings

Here you will find explanation of all different features in the settings sections and administration panel.

## Levels of management

There are three levels of management in the 4ga Boards structure:
User/Member - the lowest level, they can only access those projects and boards they have been added to;
Project Manager - can manage everything inside the project, including adding new members to it and creating new boards;
Administrator - can access every settings in the instance and create new projects.

Each tier is unlocking special settings panel.

- For each user:
[Settings](#settings) will show you your personal settings.

- For project managers:
[Project settings](#project-settings) will guide you through settings of projects inside your instance

- For administrators:
[Instance settings](#instance-settings) will explain the settings of the instance (e.g. demo.4gaboards.com)

## Dashboard access to settings

If you are an administrator and you are manager of the currently opened project, your top-right corner of the dashboard will look like this:

[Image]

If you click on your profile photo/name, you will open all the possible settings for your account:

[Image: settingsprofileclicked_en-5c128c37d429c6175deff17796b6034f.png]

To access the settings, click on the cog icon.

To access instance settings click on the users icon (to the right of the cog settings icon). It will not be visible if you don't have the administrator rights.

To quickly navigate the different settings, use the side panel - you can hide/show it using the bar&arrow icon next to 4ga Boards logo:

[Image: settingssidebar_en-f376fd9b7940079b428eecb36d4a8b55.png]

---

## Instance settings

As an administrator, you have control over instance possible login methods and users. To access it, click "Users" icon on the dashboard, or click one of the relevant options from the settings sidebar.

[Image]

## Users

In the table you can see most important information about each user: name, username, e-mail and last login time. Here you can also grant users administrator rights by toggling the switch blue.

You can manually add new users with the "add user" button here. If you have disabled user registration, new users cannot log into the instance if you didn't add them. To add new user you have to provide an e-mail, password, name and (optional) username.

As an administrator, you have control over users information. When you click on the pencil icon, you can access the all of the personal [settings](#settings) of each individual user. You can change them all without knowing their current password. Pretty handy! You can also check account activity — including when the user account was created and when the user’s information was last updated (for example, after a password change).
You can also view the user’s activity log, which lists all actions performed across projects and boards.

[Image: instanceusers_en-f3c4aa19bc6365ba5be908635d247945.png]

This is the default column selection. Similar to the list view, you can show or hide additional columns with extra information (see pictures below). You can also adjust the table to fit the content or screen, and change the width of columns using the `|` separator.

[Image: userscolumns_en-d182d0bdd345c3f5e5bf2be50948c8ce.png]

Here is the picture of all columns selected:

[Image: usersfull_en-42bf351631bc1dffc4929420ec52b41f.png]

## Instance options

- Users registration: turn off if you want to retain full control over new users (they will have to be added manually).

- Local User registration: Turn on if you want new users to be able to register using e-mail and password.

- SSO User Registration: Turn on if you want to enable registration with SSO.

- Project Creation For All Users: Enable or disable project creation for all users. If you disable project creation for all users, only admins will be able to create new projects. Select this options if you want all your users to be able to create personal projects.

- Sync SSO Data on Authentication: Enable or disable synchronization of user data from the SSO provider during authentication.

- Sync SSO Admin on Authentication: Enable or disable synchronization of admin from the SSO provider during authentication. Enabling this option can automatically grant or revoke admin rights based on the SSO data.

- Allowed Registration Domains: Semicolon separated list of allowed email domains for user registration. Leave empty to allow any domain.

[Image: instancesettings_en-5e06fc0be3956807f89207ae8447dbd9.png]

---

## Project Settings

# Project Settings

If you are manager of the project that you are currently editing, click the "cog in the box" icon in the top-right corner next to `+ Add Board` button to access this project settings.

[Image]

Alternatively you can use ellipsis menu from the sidebar to access settings to any of your managed projects. Just hover over the desired project name and click on ellipsis icon.

[Image: projectsmenu_en-74909a67e4470a6550d30825e7bd4ac3.png]

When you choose the project you have options to change the title (click `Save` button to save changes),add or remove managers from the project and set the background image (with possibility to upload your own). The image will be visible in [projects view](#project). For extra caution, the `Delete Project` button was situated in the `Danger Zone`. And no worries, we added an additional pop-up for confirmation.

[Image: projectsettings_en-a1445c6c2ffa02d3c349f1d4449b57bf.png]

To go back to the project, click on arrow icon in the top-right corner or click the 4ga Boards logo to access [projects view](#project).
