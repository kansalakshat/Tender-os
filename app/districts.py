"""Districts of every state and UT in matching.STATES, in the same order.

Based on the public sab99r/Indian-States-And-Districts list (~2020), updated
for the reorganisations since: Andhra Pradesh's 26 (2022), Rajasthan's 41 after
the 2024 rollback, Ladakh split from Jammu and Kashmir, and the renames
(Prayagraj, Narmadapuram, Chhatrapati Sambhajinagar, and so on).

"New (Old)" entries carry both names. Tender text still uses the old ones
("Bellary", "Allahabad"), so matching.district_names() splits them and either
name matches.

ponytail: a hand-kept list. New districts are created every year or two; when a
buyer's place is missing, add it here. The LGD directory is the upgrade path.
"""
from __future__ import annotations

DISTRICTS: dict[str, tuple[str, ...]] = {
    "Andhra Pradesh": (
        "Alluri Sitharama Raju", "Anakapalli", "Anantapur (Anantapuramu)", "Annamayya",
        "Bapatla", "Chittoor", "Dr. B.R. Ambedkar Konaseema", "East Godavari", "Eluru",
        "Guntur", "Kakinada", "Krishna", "Kurnool", "Nandyal", "NTR", "Palnadu",
        "Parvathipuram Manyam", "Prakasam", "Sri Potti Sriramulu Nellore (Nellore)",
        "Sri Sathya Sai", "Srikakulam", "Tirupati", "Visakhapatnam", "Vizianagaram",
        "West Godavari", "YSR Kadapa (Kadapa)",
    ),
    "Arunachal Pradesh": (
        "Anjaw", "Bichom", "Changlang", "Dibang Valley", "East Kameng", "East Siang",
        "Kamle", "Keyi Panyor", "Kra Daadi", "Kurung Kumey", "Lepa Rada", "Lohit",
        "Longding", "Lower Dibang Valley", "Lower Siang", "Lower Subansiri", "Namsai",
        "Pakke Kessang", "Papum Pare", "Shi Yomi", "Siang", "Tawang", "Tirap",
        "Upper Siang", "Upper Subansiri", "West Kameng", "West Siang",
    ),
    "Assam": (
        "Bajali", "Baksa", "Barpeta", "Biswanath", "Bongaigaon", "Cachar", "Charaideo",
        "Chirang", "Darrang", "Dhemaji", "Dhubri", "Dibrugarh", "Dima Hasao", "Goalpara",
        "Golaghat", "Hailakandi", "Hojai", "Jorhat", "Kamrup", "Kamrup Metropolitan",
        "Karbi Anglong", "Sribhumi (Karimganj)", "Kokrajhar", "Lakhimpur", "Majuli",
        "Morigaon", "Nagaon", "Nalbari", "Sivasagar", "Sonitpur",
        "South Salmara-Mankachar", "Tamulpur", "Tinsukia", "Udalguri",
        "West Karbi Anglong",
    ),
    "Bihar": (
        "Araria", "Arwal", "Aurangabad", "Banka", "Begusarai", "Bhagalpur", "Bhojpur",
        "Buxar", "Darbhanga", "East Champaran (Motihari)", "Gaya", "Gopalganj", "Jamui",
        "Jehanabad", "Kaimur (Bhabua)", "Katihar", "Khagaria", "Kishanganj",
        "Lakhisarai", "Madhepura", "Madhubani", "Munger (Monghyr)", "Muzaffarpur",
        "Nalanda", "Nawada", "Patna", "Purnia (Purnea)", "Rohtas", "Saharsa",
        "Samastipur", "Saran", "Sheikhpura", "Sheohar", "Sitamarhi", "Siwan", "Supaul",
        "Vaishali", "West Champaran (Bettiah)",
    ),
    "Chhattisgarh": (
        "Balod", "Baloda Bazar", "Balrampur", "Bastar", "Bemetara", "Bijapur", "Bilaspur",
        "Dantewada", "Dhamtari", "Durg", "Gariaband (Gariyaband)", "Gaurela-Pendra-Marwahi",
        "Janjgir-Champa", "Jashpur", "Kabirdham (Kawardha)",
        "Khairagarh-Chhuikhadan-Gandai", "Kanker", "Kondagaon", "Korba",
        "Koriya (Korea)", "Mahasamund", "Manendragarh-Chirmiri-Bharatpur",
        "Mohla-Manpur-Ambagarh Chowki", "Mungeli", "Narayanpur", "Raigarh", "Raipur",
        "Rajnandgaon", "Sakti", "Sarangarh-Bilaigarh", "Sukma", "Surajpur", "Surguja",
    ),
    "Goa": ("North Goa", "South Goa"),
    "Gujarat": (
        "Ahmedabad", "Amreli", "Anand", "Aravalli", "Banaskantha", "Bharuch",
        "Bhavnagar", "Botad", "Chhota Udepur", "Dahod", "Dang (Dangs)", "Devbhoomi Dwarka",
        "Gandhinagar", "Gir Somnath", "Jamnagar", "Junagadh", "Kutch (Kachchh)", "Kheda",
        "Mahisagar", "Mehsana", "Morbi", "Narmada", "Navsari", "Panchmahal", "Patan",
        "Porbandar", "Rajkot", "Sabarkantha", "Surat", "Surendranagar", "Tapi",
        "Vadodara", "Valsad",
    ),
    "Haryana": (
        "Ambala", "Bhiwani", "Charkhi Dadri", "Faridabad", "Fatehabad",
        "Gurugram (Gurgaon)", "Hisar", "Jhajjar", "Jind", "Kaithal", "Karnal",
        "Kurukshetra", "Mahendragarh", "Nuh (Mewat)", "Palwal", "Panchkula", "Panipat",
        "Rewari", "Rohtak", "Sirsa", "Sonipat", "Yamunanagar",
    ),
    "Himachal Pradesh": (
        "Bilaspur", "Chamba", "Hamirpur", "Kangra", "Kinnaur", "Kullu",
        "Lahaul and Spiti", "Mandi", "Shimla", "Sirmaur", "Solan", "Una",
    ),
    "Jharkhand": (
        "Bokaro", "Chatra", "Deoghar", "Dhanbad", "Dumka", "East Singhbhum", "Garhwa",
        "Giridih", "Godda", "Gumla", "Hazaribagh (Hazaribag)", "Jamtara", "Khunti", "Koderma",
        "Latehar", "Lohardaga", "Pakur", "Palamu", "Ramgarh", "Ranchi", "Sahebganj (Sahibganj)",
        "Seraikela Kharsawan (Seraikela-Kharsawan)", "Simdega", "West Singhbhum",
    ),
    "Karnataka": (
        "Bagalkot", "Ballari (Bellary)", "Belagavi (Belgaum)", "Bengaluru Rural",
        "Bengaluru Urban (Bangalore)", "Bidar", "Chamarajanagar", "Chikkaballapur (Chikballapur)",
        "Chikkamagaluru (Chikmagalur)", "Chitradurga", "Dakshina Kannada", "Davanagere (Davangere)",
        "Dharwad", "Gadag", "Hassan", "Haveri", "Kalaburagi (Gulbarga)", "Kodagu",
        "Kolar", "Koppal", "Mandya", "Mysuru (Mysore)", "Raichur", "Ramanagara",
        "Shivamogga (Shimoga)", "Tumakuru (Tumkur)", "Udupi", "Uttara Kannada",
        "Vijayanagara", "Vijayapura (Bijapur)", "Yadgir",
    ),
    "Kerala": (
        "Alappuzha", "Ernakulam", "Idukki", "Kannur", "Kasaragod", "Kollam", "Kottayam",
        "Kozhikode", "Malappuram", "Palakkad", "Pathanamthitta", "Thiruvananthapuram",
        "Thrissur", "Wayanad",
    ),
    "Madhya Pradesh": (
        "Agar Malwa", "Alirajpur", "Anuppur", "Ashoknagar", "Balaghat", "Barwani",
        "Betul", "Bhind", "Bhopal", "Burhanpur", "Chhatarpur", "Chhindwara", "Damoh",
        "Datia", "Dewas", "Dhar", "Dindori", "Guna", "Gwalior", "Harda", "Indore",
        "Jabalpur", "Jhabua", "Katni", "Khandwa", "Khargone", "Maihar", "Mandla",
        "Mandsaur", "Mauganj", "Morena", "Narmadapuram (Hoshangabad)", "Narsinghpur",
        "Neemuch", "Niwari", "Pandhurna", "Panna", "Raisen", "Rajgarh", "Ratlam", "Rewa",
        "Sagar", "Satna", "Sehore", "Seoni", "Shahdol", "Shajapur", "Sheopur",
        "Shivpuri", "Sidhi", "Singrauli", "Tikamgarh", "Ujjain", "Umaria", "Vidisha",
    ),
    "Maharashtra": (
        "Ahilyanagar (Ahmednagar)", "Akola", "Amravati", "Beed", "Bhandara", "Buldhana",
        "Chandrapur", "Chhatrapati Sambhajinagar (Aurangabad)", "Dharashiv (Osmanabad)",
        "Dhule", "Gadchiroli", "Gondia", "Hingoli", "Jalgaon", "Jalna", "Kolhapur",
        "Latur", "Mumbai City", "Mumbai Suburban", "Nagpur", "Nanded", "Nandurbar",
        "Nashik", "Palghar", "Parbhani", "Pune", "Raigad", "Ratnagiri", "Sangli",
        "Satara", "Sindhudurg", "Solapur", "Thane", "Wardha", "Washim", "Yavatmal",
    ),
    "Manipur": (
        "Bishnupur", "Chandel", "Churachandpur", "Imphal East", "Imphal West", "Jiribam",
        "Kakching", "Kamjong", "Kangpokpi", "Noney", "Pherzawl", "Senapati",
        "Tamenglong", "Tengnoupal", "Thoubal", "Ukhrul",
    ),
    "Meghalaya": (
        "East Garo Hills", "East Jaintia Hills", "East Khasi Hills",
        "Eastern West Khasi Hills", "North Garo Hills", "Ri Bhoi", "South Garo Hills",
        "South West Garo Hills", "South West Khasi Hills", "West Garo Hills",
        "West Jaintia Hills", "West Khasi Hills",
    ),
    "Mizoram": (
        "Aizawl", "Champhai", "Hnahthial", "Khawzawl", "Kolasib", "Lawngtlai", "Lunglei",
        "Mamit", "Saitual", "Serchhip", "Siaha (Saiha)",
    ),
    "Nagaland": (
        "Chumoukedima", "Dimapur", "Kiphire", "Kohima", "Longleng", "Mokokchung", "Mon",
        "Niuland", "Noklak", "Peren", "Phek", "Shamator", "Tseminyu", "Tuensang",
        "Wokha", "Zunheboto",
    ),
    "Odisha": (
        "Angul", "Balangir (Bolangir)", "Balasore (Baleswar)", "Bargarh", "Bhadrak",
        "Boudh", "Cuttack", "Deogarh", "Dhenkanal", "Gajapati", "Ganjam",
        "Jagatsinghpur (Jagatsinghapur)", "Jajpur", "Jharsuguda", "Kalahandi", "Kandhamal", "Kendrapara",
        "Kendujhar (Keonjhar)", "Khordha", "Koraput", "Malkangiri", "Mayurbhanj",
        "Nabarangpur", "Nayagarh", "Nuapada", "Puri", "Rayagada", "Sambalpur",
        "Subarnapur (Sonepur)", "Sundargarh",
    ),
    "Punjab": (
        "Amritsar", "Barnala", "Bathinda", "Faridkot", "Fatehgarh Sahib", "Fazilka",
        "Ferozepur", "Gurdaspur", "Hoshiarpur", "Jalandhar", "Kapurthala", "Ludhiana",
        "Malerkotla", "Mansa", "Moga", "Pathankot", "Patiala", "Rupnagar (Ropar)",
        "Sahibzada Ajit Singh Nagar (Mohali)", "Sangrur",
        "Shaheed Bhagat Singh Nagar (Nawanshahr)", "Sri Muktsar Sahib (Muktsar)",
        "Tarn Taran",
    ),
    "Rajasthan": (
        "Ajmer", "Alwar", "Balotra", "Banswara", "Baran", "Barmer", "Beawar",
        "Bharatpur", "Bhilwara", "Bikaner", "Bundi", "Chittorgarh", "Churu", "Dausa",
        "Deeg", "Dholpur", "Didwana-Kuchaman", "Dungarpur", "Hanumangarh", "Jaipur",
        "Jaisalmer", "Jalore", "Jhalawar", "Jhunjhunu", "Jodhpur", "Karauli",
        "Khairthal-Tijara", "Kota", "Kotputli-Behror", "Nagaur", "Pali", "Phalodi",
        "Pratapgarh", "Rajsamand", "Salumbar", "Sawai Madhopur", "Sikar", "Sirohi",
        "Sri Ganganagar", "Tonk", "Udaipur",
    ),
    "Sikkim": (
        "Gangtok (East Sikkim)", "Gyalshing (West Sikkim)", "Mangan (North Sikkim)",
        "Namchi (South Sikkim)", "Pakyong", "Soreng",
    ),
    "Tamil Nadu": (
        "Ariyalur", "Chengalpattu", "Chennai", "Coimbatore", "Cuddalore", "Dharmapuri",
        "Dindigul", "Erode", "Kallakurichi", "Kanchipuram", "Kanyakumari", "Karur",
        "Krishnagiri", "Madurai", "Mayiladuthurai", "Nagapattinam", "Namakkal",
        "Nilgiris", "Perambalur", "Pudukkottai", "Ramanathapuram", "Ranipet", "Salem",
        "Sivaganga", "Tenkasi", "Thanjavur", "Theni", "Thoothukudi (Tuticorin)",
        "Tiruchirappalli (Trichy)", "Tirunelveli", "Tirupathur", "Tiruppur",
        "Tiruvallur", "Tiruvannamalai", "Tiruvarur", "Vellore", "Viluppuram",
        "Virudhunagar",
    ),
    "Telangana": (
        "Adilabad", "Bhadradri Kothagudem", "Hanumakonda", "Hyderabad", "Jagtial",
        "Jangaon", "Jayashankar Bhupalpally (Jayashankar Bhoopalpally)", "Jogulamba Gadwal", "Kamareddy",
        "Karimnagar", "Khammam", "Komaram Bheem Asifabad", "Mahabubabad",
        "Mahabubnagar", "Mancherial", "Medak", "Medchal-Malkajgiri (Medchal)", "Mulugu",
        "Nagarkurnool", "Nalgonda", "Narayanpet", "Nirmal", "Nizamabad", "Peddapalli",
        "Rajanna Sircilla", "Ranga Reddy (Rangareddy)", "Sangareddy", "Siddipet", "Suryapet",
        "Vikarabad", "Wanaparthy", "Warangal", "Yadadri Bhuvanagiri",
    ),
    "Tripura": (
        "Dhalai", "Gomati", "Khowai", "North Tripura", "Sepahijala", "South Tripura",
        "Unakoti", "West Tripura",
    ),
    "Uttar Pradesh": (
        "Agra", "Aligarh", "Ambedkar Nagar", "Amethi", "Amroha", "Auraiya",
        "Ayodhya (Faizabad)", "Azamgarh", "Baghpat", "Bahraich", "Ballia", "Balrampur",
        "Banda", "Barabanki", "Bareilly", "Basti", "Bhadohi", "Bijnor", "Budaun",
        "Bulandshahr", "Chandauli", "Chitrakoot", "Deoria", "Etah", "Etawah",
        "Farrukhabad", "Fatehpur", "Firozabad", "Gautam Buddha Nagar (Noida)",
        "Ghaziabad", "Ghazipur", "Gonda", "Gorakhpur", "Hamirpur", "Hapur", "Hardoi",
        "Hathras", "Jalaun", "Jaunpur", "Jhansi", "Kannauj", "Kanpur Dehat",
        "Kanpur Nagar", "Kasganj", "Kaushambi", "Kushinagar", "Lakhimpur Kheri",
        "Lalitpur", "Lucknow", "Maharajganj", "Mahoba", "Mainpuri", "Mathura", "Mau",
        "Meerut", "Mirzapur", "Moradabad", "Muzaffarnagar", "Pilibhit", "Pratapgarh",
        "Prayagraj (Allahabad)", "Rae Bareli (Raebareli)", "Rampur", "Saharanpur", "Sambhal",
        "Sant Kabir Nagar", "Shahjahanpur", "Shamli", "Shravasti", "Siddharthnagar (Siddharth Nagar)",
        "Sitapur", "Sonbhadra", "Sultanpur", "Unnao", "Varanasi",
    ),
    "Uttarakhand": (
        "Almora", "Bageshwar", "Chamoli", "Champawat", "Dehradun", "Haridwar",
        "Nainital", "Pauri Garhwal", "Pithoragarh", "Rudraprayag", "Tehri Garhwal",
        "Udham Singh Nagar", "Uttarkashi",
    ),
    "West Bengal": (
        "Alipurduar", "Bankura", "Birbhum", "Cooch Behar", "Dakshin Dinajpur",
        "Darjeeling", "Hooghly", "Howrah", "Jalpaiguri", "Jhargram", "Kalimpong",
        "Kolkata", "Malda", "Murshidabad", "Nadia", "North 24 Parganas",
        "Paschim Bardhaman", "Paschim Medinipur", "Purba Bardhaman (Burdwan)", "Purba Medinipur",
        "Purulia", "South 24 Parganas", "Uttar Dinajpur",
    ),
    "Delhi": (
        "Central Delhi", "East Delhi", "New Delhi", "North Delhi", "North East Delhi",
        "North West Delhi", "Shahdara", "South Delhi", "South East Delhi",
        "South West Delhi", "West Delhi",
    ),
    "Jammu and Kashmir": (
        "Anantnag", "Bandipora (Bandipore)", "Baramulla", "Budgam", "Doda", "Ganderbal", "Jammu",
        "Kathua", "Kishtwar", "Kulgam", "Kupwara", "Poonch", "Pulwama", "Rajouri",
        "Ramban", "Reasi", "Samba", "Shopian", "Srinagar", "Udhampur",
    ),
    "Ladakh": ("Kargil", "Leh"),
    "Puducherry": ("Karaikal", "Mahe", "Puducherry (Pondicherry)", "Yanam"),
    "Chandigarh": ("Chandigarh",),
    "Andaman and Nicobar": ("Nicobar", "North and Middle Andaman", "South Andaman"),
}
