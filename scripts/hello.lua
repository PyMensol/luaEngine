local function greet(who)
    print("hey", who)
end

for _, n in ipairs({"world", "studio", "lua"}) do
    greet(n)
end
