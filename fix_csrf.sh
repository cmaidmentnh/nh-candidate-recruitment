#!/bin/bash
cd templates

# add_running.html
sed -i '' '13s/^/    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' add_running.html

# add_user.html
sed -i '' '6s/^/    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' add_user.html

# change_password.html
sed -i '' '7s/^/    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' change_password.html

# comments.html
sed -i '' '5s/^/<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' comments.html

# create_candidate.html
sed -i '' '5s/^/<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' create_candidate.html

# edit_user.html
sed -i '' '6s/^/    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' edit_user.html

# profile.html
sed -i '' '13s/^/    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' profile.html

# register.html
sed -i '' '6s/^/    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' register.html

# index.html
sed -i '' '115s/^/    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' index.html

# match_candidates.html
sed -i '' '28s/^/                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"\/>\n/' match_candidates.html

echo "Done!"
